"""Layer-aware inspection, verified edits and repeatable layer configuration."""
import json

from ..app import mcp
from ..labtalk_safe import labtalk_choice, labtalk_name, labtalk_text, positive_int
from ..origin_connection import activate_window, execute_labtalk, get_origin, require_graph, require_worksheet
from .graph_context import (
    check, finite, graph_page, layer_scope, numeric, read_number, read_plot_option,
    read_string, report, resolve_layer, read_plot_x_binding,
)
from .style_helpers import acquire_graph_layer, graph_layer_execute, get_plot_info

AXES = {'x': 'xb', 'y': 'yl', 'x2': 'xt', 'y2': 'yr'}
UNIT_CODES = {1: 'page_percent', 2: 'inch', 3: 'cm', 4: 'mm', 5: 'pixel', 6: 'point', 7: 'parent_percent'}


def inspect_layer(graph_name, index):
    with layer_scope(graph_name, index):
        gl = acquire_graph_layer(graph_name)
        result = {'layer_index': index, 'layer_id': gl.Name or None}
        result['binding_metadata']=read_string(graph_name,'layer.comments$')
        result['geometry'] = {p: read_number(graph_name, 'layer.' + p) for p in ['left','top','width','height','unit','link']}
        unit = result['geometry']['unit']
        result['geometry']['units'] = UNIT_CODES.get(int(unit)) if unit is not None else None
        result['axes'] = {}
        for axis, obj in AXES.items():
            props = {p: read_number(graph_name, f'layer.{axis}.{p}') for p in ['from','to','inc','type','rescale','thickness','ticks','label.pt','label.bold','showAxes']}
            props.update(title=read_string(graph_name, obj+'.text$'), title_size_pt=read_number(graph_name,obj+'.fsize'), title_font_index=read_number(graph_name,obj+'.font'), tick_font_index=read_number(graph_name,f'layer.{axis}.label.font'))
            result['axes'][axis] = props
        result['plots'] = []
        for i, info in enumerate(get_plot_info(graph_name),1):
            name = info['name']
            # Position-qualified graph range disambiguates repeated datasets.
            target = f'[{graph_name}]{index}!{i}'
            props = dict(info, plot_position=i, range=target)
            props['line_width_pt'] = read_plot_option(graph_name,target,'wp')
            props['line_width_raw'] = read_plot_option(graph_name,target,'w')
            props['plot_type'] = read_number(graph_name,f'layer.plot{i}.pid')
            props['symbol_size_pt'] = read_plot_option(graph_name,target,'z')
            props['group_edit_mode'] = read_plot_option(graph_name,target,'gm')
            props['source_binding'] = name
            props['x_binding'] = read_plot_x_binding(graph_name,index,i)
            result['plots'].append(props)
        result['legend'] = {p: read_number(graph_name,'legend.'+p) for p in ['show','fsize','left','top','width','height','background']}
        result['legend']['text'] = read_string(graph_name,'legend.text$')
        return result


@mcp.tool()
def inspect_graph(graph_name: str) -> str:
    """Read all graph layers, dataset bindings, axis ranges/ticks/fonts/titles,
    plot widths, legend and page dimensions in one response. Physical widths
    and fonts are in points. Geometry units are reported per layer. Unreadable
    properties are null, never proof of visual success. This does not rescale
    or rebuild anything. Layer IDs are Origin layer names and survive saves.
    """
    safe = labtalk_name(graph_name,'graph_name')
    require_graph(safe)
    activate_window(safe,'graph_name')
    original = numeric(get_origin().LTVar('page.active'))
    try:
        count = graph_page(safe).Layers.Count
        result = {'graph_name':safe, 'schema_version':1,
                  'page':{p:read_number(safe,'page.'+p) for p in ['width','height','unit','resx','resy']},
                  'layers':[inspect_layer(safe,i+1) for i in range(count)],
                  'readback_scope':'property values; inspect an export for visual confirmation'}
        result['page']['size_unit'] = 'dots; physical size uses page.resx/resy dots per inch'
        page=result['page']
        page['width_mm']=page['width']/page['resx']*25.4 if page['width'] is not None and page['resx'] else None
        page['height_mm']=page['height']/page['resy']*25.4 if page['height'] is not None and page['resy'] else None
        return json.dumps(result,allow_nan=False)
    finally:
        if original:
            execute_labtalk(f'page.active={int(original)};')


@mcp.tool()
def configure_layer(
    graph_name: str, changes: str, layer_index: int | None = None,
    layer_id: str | None = None,
) -> str:
    """Apply a validated JSON property patch and return structured readback.

    Example changes: {"axes":{"x":{"from":0,"to":64,"inc":16},
    "y2":{"title":"Temperature (K)","title_size_pt":24,"tick_size_pt":20}},
    "geometry":{"units":"parent_percent","left":0,"top":0,"width":100,"height":100}}.
    Axis keys x/y/x2/y2 mean bottom/left/top/right. Fonts and thickness use
    points. No auto range, legend update, or source-data editing is performed.
    Unknown fields and non-finite values are rejected before any mutation.
    """
    safe=labtalk_name(graph_name,'graph_name')
    patch=json.loads(changes)
    if not isinstance(patch,dict) or not patch or set(patch)-{'axes','geometry'}:
        raise ValueError('changes must contain axes and/or geometry objects.')
    commands=[]
    expected={}
    axis_props={'from':'from','to':'to','inc':'inc','scale':'type','thickness_pt':'thickness','tick_size_pt':'label.pt','tick_bold':'label.bold'}
    axes=patch.get('axes',{})
    if not isinstance(axes,dict):
        raise ValueError('axes must be an object.')
    for axis, props in axes.items():
        labtalk_choice(axis,AXES,'axis')
        if not isinstance(props,dict) or set(props)-set(axis_props)-{'title','title_size_pt','title_font','tick_font','title_bold'}:
            raise ValueError('Unknown axis property.')
        for key,value in props.items():
            if key=='title':
                expr=AXES[axis]+'.text$'; encoded=labtalk_text(value,'title')
            elif key in {'title_font','tick_font'}:
                expr=AXES[axis]+'.font' if key=='title_font' else f'layer.{axis}.label.font'
                encoded='font('+labtalk_text(value,key)+')'
                # The request is kept as a font name; its evaluated index is
                # compared below without claiming a guessed font substitution.
                expected[expr]={'font_name':value,'expression':encoded}
            elif key=='title_bold':
                if not isinstance(value,bool):raise ValueError('title_bold must be boolean.')
                # Resolve the current text only after the full patch validates.
                commands.append(('bold',AXES[axis],value))
                continue
            else:
                if key=='scale':
                    value={'linear':1,'log10':2,'ln':8,'log2':9}[labtalk_choice(value,{'linear','log10','ln','log2'},'scale')]
                elif key=='tick_bold':
                    if not isinstance(value,bool):
                        raise ValueError('tick_bold must be boolean.')
                    value=int(value)
                else:
                    value=finite(value,key,positive=key in {'inc','thickness_pt','tick_size_pt','title_size_pt'})
                expr=AXES[axis]+'.fsize' if key=='title_size_pt' else f'layer.{axis}.{axis_props[key]}'
                encoded=str(value)
            commands.append(f'{expr}={encoded};')
            if key not in {'title_font','tick_font'}:
                expected[expr]=value
    geometry=patch.get('geometry',{})
    if not isinstance(geometry,dict) or set(geometry)-{'left','top','width','height','units'}:
        raise ValueError('Unknown geometry property.')
    if geometry:
        labtalk_choice(geometry.get('units','current'),{'current','page_percent','parent_percent','inch','cm','mm','pixel','point'},'units')
        for key,value in geometry.items():
            if key!='units':
                finite(value,key,positive=key in {'width','height'})
    with layer_scope(safe,layer_index,layer_id) as index:
        require_graph(safe)
        font_requests={}
        for expr,value in list(expected.items()):
            if isinstance(value,dict):
                font_requests[expr]=value['font_name']
                expected[expr]=read_number(safe,value['expression'])
                if expected[expr] is not None and expected[expr]<=0:
                    raise ValueError('Font is not available: '+value['font_name'])
        if geometry:
            from .graph import set_layer_geometry
            set_layer_geometry(safe,**geometry,layer_index=index)
            expected.update({'layer.'+k:v for k,v in geometry.items() if k!='units'})
        bold_requests={}
        # Apply title text first so JSON property order cannot undo bold.
        ordered = [c for c in commands if not isinstance(c, tuple)]
        ordered += [c for c in commands if isinstance(c, tuple)]
        for command in ordered:
            if isinstance(command,tuple):
                _,obj,bold=command
                text=read_string(safe,obj+'.text$')
                if text is None:
                    bold_requests[obj]=None
                    continue
                plain=text[3:-1] if text.startswith('\\b(') and text.endswith(')') else text
                output='\\b('+plain+')' if bold else plain
                command=obj+'.text$='+labtalk_text(output,'title')+';'
                bold_requests[obj]=bold
                if obj+'.text$' in expected:
                    expected[obj+'.text$']=output
            if not graph_layer_execute(safe,command):
                raise ValueError('Origin rejected a layer property write: '+command)
        graph_layer_execute(safe,'doc -uw;')
        checks={expr:check(value,read_string(safe,expr) if expr.endswith('$') else read_number(safe,expr),tolerance=0.01 if expr.startswith('layer.') and expr.split('.')[-1] in {'left','top','width','height'} else 1e-6) for expr,value in expected.items()}
        for expr,name in font_requests.items():
            checks[expr]['requested_font_name']=name
        for obj,bold in bold_requests.items():
            text=read_string(safe,obj+'.text$')
            checks[obj+'.bold']=check(bold,None if text is None else text.startswith('\\b('))
        return json.dumps(report(safe,index,checks),allow_nan=False)


def snapshot_state(graph_name):
    """Snapshot existing layers before operations known to trigger auto scaling."""
    page=graph_page(graph_name)
    snapshots=[]
    for i in range(page.Layers.Count):
        with layer_scope(graph_name,i+1):
            props={}
            for axis in ['x','y']:
                for key in ['from','to','inc','type','rescale']:
                    expr=f'layer.{axis}.{key}'
                    props[expr]=read_number(graph_name,expr)
            for key in ['unit','left','top','width','height']:
                expr='layer.'+key; props[expr]=read_number(graph_name,expr)
            snapshots.append(props)
    return snapshots


def restore_state(graph_name,snapshots):
    failures=[]
    for i,props in enumerate(snapshots,1):
        with layer_scope(graph_name,i):
            resize_parent=read_number(graph_name,'layer.ResizeParent')
            graph_layer_execute(graph_name,'layer.ResizeParent=0;')
            # Disable auto rescale while restoring from/to/inc; restore policy last.
            graph_layer_execute(graph_name,'layer.x.rescale=0; layer.y.rescale=0;')
            ordered=sorted(props,key=lambda p:p.endswith('.rescale'))
            for expr in ordered:
                value=props[expr]
                if value is None:
                    continue
                if not graph_layer_execute(graph_name,f'{expr}={value:g};'):
                    failures.append(expr)
            for expr,value in props.items():
                if value is not None and check(value,read_number(graph_name,expr),tolerance=0.01)['status']=='not_applied':
                    failures.append(f'layer {i}: {expr}')
            if resize_parent is not None:
                graph_layer_execute(graph_name,f'layer.ResizeParent={resize_parent:g};')
    if failures:
        raise ValueError('Existing graph state could not be restored: '+', '.join(failures))


@mcp.tool()
def ensure_graph_layer(
    graph_name: str, layer_id: str, layer_type: str = 'right-y',
    parent_layer_index: int = 1, overlap_parent: bool = True,
    share_x: bool = True,
) -> str:
    """Create or update a persistent named layer. Repeated calls reuse it.
    right-y exposes y2 and the YR title. Overlap uses 0,0,100,100 in parent
    percent; shared X links scales 1:1. Existing layers keep ranges/ticks and
    geometry. No legend is rebuilt. Specify all targets; active window is ignored.
    """
    safe=labtalk_name(graph_name,'graph_name'); identity=labtalk_name(layer_id,'layer_id')
    kind=labtalk_choice(layer_type,{'right-y','top-x','inset','independent'},'layer_type')
    parent=resolve_layer(safe,parent_layer_index)
    page=graph_page(safe)
    matches=[i+1 for i in range(page.Layers.Count) if page.Layers.Item(i).Name==identity]
    if len(matches)>1:
        raise ValueError('Duplicate layer IDs; refusing an ambiguous update.')
    snapshots=snapshot_state(safe)
    created=not matches
    index=matches[0] if matches else page.Layers.Count+1
    if index<=parent:
        raise ValueError('A child must follow its parent in the layer order.')
    try:
        if created:
            activate_window(safe,'graph_name'); execute_labtalk(f'page.active={parent};')
            native={'right-y':'righty','top-x':'topx','inset':'inset','independent':'normal'}[kind]
            if not execute_labtalk(f'layadd igp:={safe} type:={native};'):
                raise ValueError('Origin rejected layer creation.')
            if graph_page(safe).Layers.Count!=index:
                raise ValueError('Layer creation did not produce exactly one layer.')
            with layer_scope(safe,index):
                if not graph_layer_execute(safe,f'layer.name$="{identity}";'):
                    raise ValueError('Origin rejected the layer ID.')
        with layer_scope(safe,index):
            if share_x or overlap_parent:
                if not execute_labtalk(f'laylink igp:={safe} igl:={parent} destlayers:={index} XAxis:={int(share_x)} unit:=link;'):
                    raise ValueError('Origin rejected the layer link.')
            if overlap_parent:
                from .graph import set_layer_geometry
                set_layer_geometry(safe,left=0,top=0,width=100,height=100,units='parent_percent',layer_index=index)
    finally:
        # Updating an existing named layer intentionally changes its own geometry.
        restore_state(safe,[s if i+1!=index else {} for i,s in enumerate(snapshots)])
    if resolve_layer(safe,layer_id=identity)!=index:
        raise ValueError('Layer identity did not persist.')
    return json.dumps({'graph_name':safe,'layer_index':index,'layer_id':identity,'created':created,'visible_y_axis':'y2' if kind=='right-y' else 'y','title_object':'YR' if kind=='right-y' else 'YL','inspection':inspect_layer(safe,index)})


@mcp.tool()
def set_group_edit_mode(graph_name: str, independent: bool = True, layer_index: int | None = None, layer_id: str | None = None) -> str:
    """Change group edit mode in place using -gm; retain plot types, stacking,
    error bars and bindings. This does not ungroup/replot. Readback failures
    remain explicit in the returned structured report.
    """
    with layer_scope(graph_name,layer_index,layer_id) as index:
        props={}
        for i,info in enumerate(get_plot_info(graph_name),1):
            if info['is_error']:
                continue
            target=f'[{graph_name}]{index}!{i}'
            before=read_number(graph_name,f'layer.plot{i}.pid')
            if not graph_layer_execute(graph_name,f'range __mcp_plot={target}; set __mcp_plot -gm {int(independent)};'):
                raise ValueError('Origin rejected group edit mode.')
            props[target+'.group_mode']=check(int(independent),read_plot_option(graph_name,target,'gm'))
            if before is not None:
                props[target+'.plot_type']=check(before,read_number(graph_name,f'layer.plot{i}.pid'))
        if not props:
            raise ValueError('No editable plots found.')
        return json.dumps(report(graph_name,index,props))
