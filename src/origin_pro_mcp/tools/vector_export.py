"""Conservative SVG transformations. Never mutate an Origin source graph."""
import copy
import json
import os
from pathlib import Path
import re
import tempfile
import xml.etree.ElementTree as ET

from ..labtalk_safe import labtalk_choice, labtalk_name, labtalk_string, windows_path
from ..origin_connection import activate_window, execute_labtalk, require_graph
from .graph_context import resolve_layer

SVG='http://www.w3.org/2000/svg'
OLAB='http://www.originlab.com/namespaces/olab'
ET.register_namespace('',SVG)
ET.register_namespace('xlink','http://www.w3.org/1999/xlink')
ET.register_namespace('olab',OLAB)


def parse_svg(data):
    # Origin emits a PUBLIC doctype without an internal subset. Reject entities.
    if re.search(br'<!ENTITY|<!DOCTYPE[^>]*\[',data,re.I):
        raise ValueError('SVG entities and internal DTDs are unsupported.')
    root=ET.fromstring(data)
    if root.tag!='{'+SVG+'}svg':
        raise ValueError('Export is not an SVG document.')
    return root


def rectangular_mask(mask):
    if set(mask.attrib)-{'id','maskUnits','maskContentUnits'}:
        return None
    if mask.get('maskUnits','userSpaceOnUse')!='userSpaceOnUse' or mask.get('maskContentUnits','userSpaceOnUse')!='userSpaceOnUse':
        return None
    if len(mask)!=1 or mask[0].tag!='{'+SVG+'}rect':
        return None
    rect=mask[0]
    if set(rect.attrib)-{'x','y','width','height','fill'} or rect.get('fill','').lower() not in {'white','#fff','#ffffff'}:
        return None
    try:
        box=[float(rect.get(k,'0')) for k in ['x','y','width','height']]
    except ValueError:
        return None
    if box[2]<=0 or box[3]<=0:
        return None
    return box


def convert_rectangular_masks(root):
    converted=[]; unsupported=[]
    for mask in list(root.iter('{'+SVG+'}mask')):
        ident=mask.get('id')
        if not ident or rectangular_mask(mask) is None:
            unsupported.append(ident)
            continue
        # CSS-based references need a CSS parser; preserve instead of guessing.
        if any('mask:' in n.get('style','').replace(' ','') or (n.tag=='{'+SVG+'}style' and ident in (n.text or '')) for n in root.iter()):
            unsupported.append(ident)
            continue
        mask.tag='{'+SVG+'}clipPath'
        mask.attrib={'id':ident,'clipPathUnits':'userSpaceOnUse'}
        mask[0].attrib.pop('fill',None)
        for node in root.iter():
            if node.get('mask')==f'url(#{ident})':
                node.attrib.pop('mask'); node.set('clip-path',f'url(#{ident})')
        converted.append(ident)
    return {'converted_masks':converted,'unsupported_masks':unsupported}


def crop_layer(root,index,plots_only=False):
    """Use Origin's semantic scope, never crop away partial glyphs by a box alone."""
    prefix=f'Layer{index-1}'
    scope_key='{'+OLAB+'}scope'
    scoped=[n for n in root.iter() if n.get(scope_key,'').split('.')[0]==prefix]
    if not scoped:
        raise ValueError('SVG has no Origin semantic layer scopes; layer crop cannot be verified.')
    parents={child:parent for parent in root.iter() for child in parent}
    masks={n.get('id'):n for n in root.iter('{'+SVG+'}mask')}
    boxes=[]
    for node in scoped:
        if '.Plot' not in node.get(scope_key,''):
            continue
        parent=node
        while parent in parents:
            reference=parent.get('mask','')
            match=re.fullmatch(r'url\(#([^\)]+)\)',reference)
            if match and match[1] in masks:
                box=rectangular_mask(masks[match[1]])
                if box and box not in boxes:
                    boxes.append(box)
            parent=parents[parent]
    if plots_only and len(boxes)!=1:
        raise ValueError('Plot-area crop needs one unambiguous rectangular Origin plot mask.')

    def prune(parent):
        for node in list(parent):
            if node.tag in {'{'+SVG+'}defs','{'+SVG+'}metadata'}:
                continue
            scope=node.get(scope_key)
            if scope is not None:
                keep=scope.split('.')[0]==prefix and (not plots_only or '.Plot' in scope)
                if not keep:
                    parent.remove(node); continue
                # Keep a semantically selected object's full text/glyph subtree.
                continue
            elif node.tag not in {'{'+SVG+'}g','{'+SVG+'}svg'}:
                # Unscoped page decorations cannot be assigned to this layer.
                parent.remove(node); continue
            prune(node)
            if node.tag=='{'+SVG+'}g' and len(node)==0:
                parent.remove(node)
    prune(root)
    if plots_only:
        set_viewbox(root,boxes[0])
    else:
        # MuPDF computes visual content bounds including complete text glyphs.
        try:
            import pymupdf as fitz
        except ImportError as exc:
            raise ValueError('Layer content crop requires optional PyMuPDF (install origin-pro-mcp[vector]).') from exc
        svg=ET.tostring(root)
        doc=fitz.open(stream=svg,filetype='svg')
        pdf=fitz.open(stream=doc.convert_to_pdf(),filetype='pdf')
        page=pdf[0]
        boxes_pdf=[entry[1] for entry in page.get_bboxlog() if entry[0] != 'ignore-text']
        if not boxes_pdf:
            raise ValueError('Layer has no visible content.')
        bbox=fitz.Rect(boxes_pdf[0])
        for b in boxes_pdf[1:]:
            bbox |= fitz.Rect(b)
        vb=[float(x) for x in re.split('[ ,]+',root.get('viewBox','').strip())]
        sx=vb[2]/page.rect.width; sy=vb[3]/page.rect.height
        set_viewbox(root,[vb[0]+bbox.x0*sx,vb[1]+bbox.y0*sy,bbox.width*sx,bbox.height*sy])
        pdf.close();doc.close()
    return {'semantic_layer_index':index,'plots_only':plots_only,'axis_objects_removed':plots_only}


def set_viewbox(root,box):
    old=[float(x) for x in re.split('[ ,]+',root.get('viewBox','').strip())]
    root.set('viewBox',' '.join(f'{n:g}' for n in box))
    # Preserve the native physical scale when cropping.
    for attr,ratio in [('width',box[2]/old[2]),('height',box[3]/old[3])]:
        val=root.get(attr,'')
        m=re.fullmatch(r'([0-9.eE+-]+)(in|cm|mm|pt|px)?',val)
        if m:
            root.set(attr,f'{float(m[1])*ratio:g}{m[2] or ""}')


def native_export(graph_name,path,format,area,width=1200):
    directory=str(Path(path).parent); filename=Path(path).stem
    labtalk_string(directory,'file_path');labtalk_string(filename,'file_path')
    raster=format in {'png','jpg','bmp','tif'}
    size=f' tr1.unit:=2 tr1.width:={width}' if raster else ''
    margin={'page':2,'content':1}[area]
    cmd=(f'expGraph export:=specified pages:={graph_name} type:={format} '
         f'path:="{directory}" filename:="{filename}" overwrite:=replace '
         f'tr.Margin:={margin} tr.SpeedMode:=2{size};')
    if not execute_labtalk(cmd) or not Path(path).is_file() or Path(path).stat().st_size==0:
        raise ValueError(f'Origin did not create a fresh {format.upper()} export. This capability is unavailable on this installation.')
    if format=='svg':
        parse_svg(Path(path).read_bytes())
    elif format=='pdf' and not Path(path).read_bytes().startswith(b'%PDF-'):
        raise ValueError('Origin output is not a PDF.')


def export_with_options(graph_name,file_path,format='svg',area='page',layer_index=None,layer_id=None,powerpoint_compatible=False,width=1200):
    safe=labtalk_name(graph_name,'graph_name'); require_graph(safe)
    fmt=labtalk_choice(format,{'svg','pdf','png','jpg','bmp','tif','eps','emf'},'format')
    area=labtalk_choice(area,{'page','content','layer','plot_area'},'area')
    if powerpoint_compatible and fmt!='svg':
        raise ValueError('PowerPoint compatibility processing is available for SVG.')
    if area in {'layer','plot_area'} and layer_index is None and not layer_id:
        raise ValueError('Layer export requires layer_index or layer_id.')
    if area in {'page','content'} and (layer_index is not None or layer_id):
        raise ValueError('A layer selector requires area=layer or plot_area.')
    if area in {'layer','plot_area'} and fmt not in {'svg','pdf'}:
        raise ValueError('Semantic layer cropping supports SVG/PDF only.')
    path=Path(windows_path(file_path,'file_path'))
    if not path.suffix:
        path=path.with_suffix('.'+fmt)
    if path.suffix.lower()!='.'+fmt:
        raise ValueError('File extension must match format.')
    index=resolve_layer(safe,layer_index,layer_id) if area in {'layer','plot_area'} else None
    path.parent.mkdir(parents=True,exist_ok=True)
    activate_window(safe,'graph_name')
    metadata={'graph_name':safe,'format':fmt,'area':area,'layer_index':index,'path':str(path),'source_graph_modified':False}
    with tempfile.TemporaryDirectory(prefix='origin-export-',dir=path.parent) as temp:
        needs_svg=fmt=='svg' or area in {'layer','plot_area'}
        native_fmt='svg' if needs_svg else fmt
        native=Path(temp)/('native.'+native_fmt)
        native_export(safe,str(native),native_fmt,'content' if area=='content' else 'page',width)
        data=native.read_bytes()
        if needs_svg:
            root=parse_svg(data)
            if index:
                metadata['crop']=crop_layer(root,index,area=='plot_area')
            if powerpoint_compatible:
                metadata['compatibility']=convert_rectangular_masks(root)
                if metadata['compatibility']['unsupported_masks']:
                    raise ValueError('Non-rectangular, translucent, transformed or CSS masks cannot safely become clip paths.')
            if index or powerpoint_compatible:
                raw=path.with_name(path.stem+'.native.svg')
                raw.write_bytes(data); metadata['native_export']=str(raw)
                data=ET.tostring(root,encoding='utf-8',xml_declaration=True)
            if fmt=='pdf':
                try:
                    import pymupdf as fitz
                except ImportError as exc:
                    raise ValueError('Cropped PDF requires optional PyMuPDF (install origin-pro-mcp[vector]).') from exc
                doc=fitz.open(stream=data,filetype='svg')
                data=doc.convert_to_pdf();doc.close()
                metadata['pdf_backend']='MuPDF SVG-to-PDF vector conversion'
        staged=Path(temp)/('final.'+fmt);staged.write_bytes(data)
        os.replace(staged,path)
    metadata['bytes']=path.stat().st_size
    metadata['visual_verification']='not performed by export; inspect a rendered preview'
    return json.dumps(metadata)
