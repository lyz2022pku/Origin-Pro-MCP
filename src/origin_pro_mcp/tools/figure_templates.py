"""Versioned figure families with independent data, layout and style inputs."""
import json
import hashlib
from pathlib import Path

from ..app import mcp
from ..labtalk_safe import labtalk_choice, labtalk_name, labtalk_text, positive_column, positive_int
from ..origin_connection import execute_labtalk, get_lt_str, get_origin, graph_names, require_matrix, require_worksheet
from .graph_context import finite, layer_scope, read_number, read_string
from .style_helpers import graph_layer_execute

CATALOG=Path(__file__).resolve().parent.parent/'templates/figure_templates.v1.json'


@mcp.tool()
def list_figure_templates() -> str:
    """List versioned generic figure families and optional source-style profiles.
    Data mappings, layout and style are separate; no statistics are inferred.
    """
    return CATALOG.read_text(encoding='utf-8')


def validate_spec(template_name,mapping,layout,style,version):
    catalog=json.loads(CATALOG.read_text(encoding='utf-8'))
    if version!=catalog['schema_version']:
        raise ValueError('Unsupported template version.')
    labtalk_choice(template_name,catalog['templates'],'template_name')
    if not all(isinstance(x,dict) for x in [mapping,layout,style]):
        raise ValueError('data_mapping, layout and style must be JSON objects.')
    if set(layout)-{'main','right','inset','color_scale','z_min','z_max','levels','palette'}:
        raise ValueError('Unknown layout key.')
    if set(style)-{'profile','font','bold','title_size_pt','tick_size_pt','axis_width_pt','line_width_pt'}:
        raise ValueError('Unknown style key.')
    resolved={}
    if 'profile' in style:
        labtalk_choice(style['profile'],catalog['optional_style_profiles'],'profile')
        resolved.update(catalog['optional_style_profiles'][style['profile']])
    resolved.update({k:v for k,v in style.items() if k!='profile'})
    for k,v in resolved.items():
        if k=='font':labtalk_text(v,'font')
        elif k=='bold':
            if not isinstance(v,bool):raise ValueError('bold must be boolean.')
        else:finite(v,k,positive=True)
    if template_name=='heatmap':
        if set(mapping)!={'matrix_book','coordinates'}:
            raise ValueError('Heatmap needs matrix_book and explicit coordinates.')
        labtalk_name(mapping['matrix_book'],'matrix_book')
        coords=mapping['coordinates']
        if not isinstance(coords,dict) or set(coords)!={'x_min','x_max','y_min','y_max'}:
            raise ValueError('Heatmap needs four explicit matrix coordinate bounds.')
        for k,v in coords.items():finite(v,k)
        if coords['x_max']<=coords['x_min'] or coords['y_max']<=coords['y_min']:
            raise ValueError('Matrix coordinate bounds must increase.')
        scale=labtalk_choice(layout.get('color_scale','linear'),{'linear','log10','zero_centered'},'color_scale')
        if scale!='linear' and not {'z_min','z_max'}<=layout.keys():
            raise ValueError('Log/zero-centered color scales require explicit z_min and z_max.')
        if ('z_min' in layout)!=('z_max' in layout):raise ValueError('Provide z_min and z_max together.')
        if 'z_min' in layout:
            lo=finite(layout['z_min'],'z_min');hi=finite(layout['z_max'],'z_max')
            if lo>=hi:raise ValueError('z_max must exceed z_min.')
            if scale=='log10' and lo<=0:raise ValueError('Log color scale requires positive bounds.')
            if scale=='zero_centered' and abs(lo+hi)>1e-9:raise ValueError('zero_centered requires symmetric explicit bounds; no data are shifted.')
    else:
        allowed={'series'}|({'right'} if template_name=='dual_y' else set())
        if set(mapping)-allowed or not isinstance(mapping.get('series'),list) or not mapping['series']:
            raise ValueError('data_mapping requires a non-empty series array.')
        if template_name=='dual_y' and not isinstance(mapping.get('right'),dict):raise ValueError('dual_y requires right mapping.')
        for item in mapping['series']+([mapping['right']] if template_name=='dual_y' else []):
            required={'data_book','data_sheet','x_col','y_col'}
            allowed_fields=required|{'plot_type','error_col','error_kind','y_cols'}
            if not isinstance(item,dict) or not required<=item.keys() or set(item)-allowed_fields:
                raise ValueError('Series requires data_book, data_sheet, x_col and y_col.')
            labtalk_name(item['data_book'],'data_book');labtalk_name(item['data_sheet'],'data_sheet')
            positive_column(item['x_col'],'x_col');positive_column(item['y_col'],'y_col')
            labtalk_choice(item.get('plot_type','line'),{'line','scatter','line+symbol','column'},'plot_type')
            if item.get('error_col'):
                positive_column(item['error_col'],'error_col')
                labtalk_choice(item.get('error_kind',''),{'SD','SEM','CI'},'error_kind')
            elif 'error_kind' in item:raise ValueError('error_kind requires an error_col.')
            if template_name=='grouped_stacked_column':
                cols=item.get('y_cols')
                if not isinstance(cols,list) or len(cols)<2 or cols[0]!=item['y_col']:
                    raise ValueError('Stacked columns need at least two y_cols starting at y_col.')
                for c in cols:positive_column(c,'y_cols')
                if cols!=list(range(cols[0],cols[-1]+1)):
                    raise ValueError('Stacked y_cols must be consecutive; source data are not rearranged.')
                if item.get('error_col'):raise ValueError('Stacked error bars need an explicit existing Origin template; no inference is performed.')
    return resolved


@mcp.tool()
def apply_figure_template(graph_name: str, template_name: str, data_mapping: str,
                          layout: str = '{}', style: str = '{}', version: int = 1) -> str:
    """Create/update a generic template using existing explicit data bindings.

    data_mapping for XY: {"series":[{"data_book":"D","data_sheet":"Sheet1",
    "x_col":1,"y_col":2,"error_col":3,"error_kind":"SD"}]}; error_col is optional.
    dual_y additionally needs "right":{...}. inset reuses series with independent
    layout.inset axes/ranges and geometry. grouped_stacked_column series require
    consecutive y_cols, with grouping positions already supplied in x_col.
    Heatmap needs matrix_book and coordinates:{x_min,x_max,y_min,y_max}; values
    must match the existing matrix (templates never rewrite coordinates/data).
    layout.main/right/inset use configure_layer JSON patches. Heatmap layout:
    color_scale=linear/log10/zero_centered, z_min/z_max, palette and levels.
    zero_centered requires symmetric bounds, without transforming input data.
    style is optional: font, bold, title_size_pt, tick_size_pt, axis_width_pt,
    line_width_pt or profile=large_source_arial. All sizes are points.
    Existing graphs must have this template's matching ownership metadata;
    repeated calls update layout/style without duplicate layers/plots/books.
    Changing bindings requires a new graph name; no implicit replacement.
    """
    safe=labtalk_name(graph_name,'graph_name')
    mapping=json.loads(data_mapping);lay=json.loads(layout);sty=json.loads(style)
    styling=validate_spec(template_name,mapping,lay,sty,version)
    from .graph import create_graph,add_plot_to_graph,add_second_y_axis,colormap
    from .graph_edit import configure_layer,ensure_graph_layer,inspect_graph,set_group_edit_mode
    from .matrix import create_matrix_plot
    from .style import set_plot_style
    if template_name=='heatmap':
        book=mapping['matrix_book'];require_matrix(book)
        from .matrix import read_matrix_coordinates
        coordinates=read_matrix_coordinates(book)
        for k,v in mapping['coordinates'].items():
            prop={'x_min':'x1','x_max':'x2','y_min':'y1','y_max':'y2'}[k]
            got=coordinates[prop]
            if got is None or abs(got-v)>1e-6:raise ValueError(f'Matrix coordinate {k} is {got}, expected {v}; configure the matrix explicitly first.')
    else:
        for item in mapping['series']+([mapping['right']] if template_name=='dual_y' else []):
            require_worksheet(item['data_book'],item['data_sheet'])
    mapping_digest=hashlib.sha256(json.dumps(mapping,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    marker=f'origin_mcp_template:{version}:{template_name}:{mapping_digest}'
    exists=safe in graph_names()
    if exists:
        with layer_scope(safe,1):
            if read_string(safe,'layer.comments$')!=marker:
                raise ValueError('Existing graph ownership/data mapping differs; use a new graph name.')
    else:
        if template_name=='heatmap':
            made=json.loads(create_matrix_plot(mapping['matrix_book'],'heatmap',safe))
        elif template_name=='grouped_stacked_column':
            # Native grouped stacks preserve grouping and cumulative behavior.
            name=get_origin().CreatePage(3,safe,'origin')
            for item in mapping['series']:
                cols=item['y_cols'];ref=f"[{item['data_book']}]{item['data_sheet']}!({item['x_col']},{cols[0]}:{cols[-1]})"
                if not execute_labtalk(f'plotxy iy:={ref} plot:=213 ogl:=[{name}]1!;'):
                    raise ValueError('Native stacked column plotting failed.')
            made={'name':name}
        else:
            def args(item):
                return {k:item[k] for k in ['data_book','data_sheet','x_col','y_col']}|{'plot_type':item.get('plot_type','line'),'y_error_col':item.get('error_col',0)}
            made=json.loads(create_graph(safe,**args(mapping['series'][0])))
            for item in mapping['series'][1:]:add_plot_to_graph(made['name'],**args(item))
        if made['name']!=safe:raise ValueError('Origin renamed the graph unexpectedly; inspect the created page before retrying.')
        graph_layer_execute(safe,'layer.name$="mcpMain";')
        graph_layer_execute(safe,'layer.comments$='+labtalk_text(marker,'template metadata')+';')
    if template_name=='dual_y':
        item=mapping['right']
        add_second_y_axis(safe,**{k:item[k] for k in ['data_book','data_sheet','x_col','y_col']},plot_type=item.get('plot_type','line'),y_error_col=item.get('error_col',0),layer_id='mcpRightY')
    elif template_name=='inset':
        info=json.loads(ensure_graph_layer(safe,'mcpInset','inset',1,False,False))
        if info['created']:
            for item in mapping['series']:
                add_plot_to_graph(safe,**{k:item[k] for k in ['data_book','data_sheet','x_col','y_col']},plot_type=item.get('plot_type','line'),y_error_col=item.get('error_col',0),layer_id='mcpInset')
            with layer_scope(safe,layer_id='mcpInset'):
                graph_layer_execute(safe,'layer.color=color(white); legend.show=0;')
                flags=read_number(safe,'page.cntrl')
                if flags is None:raise ValueError('Cannot verify sequential layer rendering for the inset.')
                graph_layer_execute(safe,f'page.cntrl={int(flags)|4};')
    checks=[]
    for key,identity in [('main','mcpMain'),('right','mcpRightY'),('inset','mcpInset')]:
        if key in lay:checks.append(json.loads(configure_layer(safe,json.dumps(lay[key]),layer_id=identity)))
    inspection=json.loads(inspect_graph(safe))
    for layer in inspection['layers']:
        identity=layer['layer_id'];right=identity=='mcpRightY'
        axes=['x','y2'] if right else ['x','y']
        patch={}
        for axis in axes:
            props={}
            for key,dest in [('font','title_font'),('title_size_pt','title_size_pt'),('tick_size_pt','tick_size_pt'),('axis_width_pt','thickness_pt'),('bold','tick_bold')]:
                if key in styling:props[dest]=styling[key]
            if 'font' in styling:props['tick_font']=styling['font']
            if 'bold' in styling:props['title_bold']=styling['bold']
            if props:patch[axis]=props
        if patch:checks.append(json.loads(configure_layer(safe,json.dumps({'axes':patch}),layer_id=identity)))
        if 'line_width_pt' in styling and template_name not in {'heatmap','grouped_stacked_column'}:
            for i,p in enumerate([p for p in layer['plots'] if not p['is_error']],1):
                set_plot_style(safe,i,line_width=styling['line_width_pt'],layer_id=identity)
    if template_name=='heatmap':
        with layer_scope(safe,1):
            scale=lay.get('color_scale','linear');native=2 if scale=='log10' else 1
            graph_layer_execute(safe,f'layer.cmap.type={native};')
            if any(k in lay for k in ['palette','z_min','levels']):
                colormap(safe,**{k:lay[k] for k in ['palette','z_min','z_max','levels'] if k in lay})
            graph_layer_execute(safe,'layer.cmap.setLevels(1); layer.cmap.updateScale();')
            if read_number(safe,'layer.cmap.type')!=native:raise ValueError('Color scale type did not take.')
    return json.dumps({'graph_name':safe,'template_name':template_name,'version':version,'created':not exists,'error_statistics':'explicit input only; no calculation','verification':checks,'inspection':json.loads(inspect_graph(safe))})
