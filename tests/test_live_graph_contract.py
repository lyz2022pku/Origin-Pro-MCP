"""Synthetic Origin round trips. Always use a new owned Application instance."""
import json
from pathlib import Path

import pytest

pytestmark=pytest.mark.requires_origin


@pytest.fixture(scope='module')
def owned_origin():
    import pythoncom
    import win32com.client
    from origin_pro_mcp.origin_connection import set_session_origin,clear_session_origin
    pythoncom.CoInitialize()
    origin=win32com.client.DispatchEx('Origin.Application')
    def refuse_shared_reconnect():
        raise RuntimeError('The owned test Origin disconnected; refusing any shared-instance reconnect.')
    set_session_origin(origin,factory=refuse_shared_reconnect)
    origin.Visible=0
    try:
        yield origin
    finally:
        try:
            origin.Exit()
        finally:
            clear_session_origin()
            pythoncom.CoUninitialize()


def test_synthetic_contract_and_templates(owned_origin,tmp_path):
    from origin_pro_mcp.tools.worksheet import create_worksheet,set_worksheet_data,get_worksheet_data
    from origin_pro_mcp.tools.matrix import create_matrix,set_matrix_data,get_matrix_data
    from origin_pro_mcp.tools.figure_templates import apply_figure_template
    from origin_pro_mcp.tools.graph import export_graph,axis
    from origin_pro_mcp.tools.graph_edit import configure_layer,inspect_graph,set_group_edit_mode
    from origin_pro_mcp.tools.style import set_plot_style,ungroup_plots,set_graph_font
    from origin_pro_mcp.tools.graph_context import read_plot_option,layer_scope
    from origin_pro_mcp.tools.vector_export import parse_svg
    import pymupdf as fitz

    w=json.loads(create_worksheet('ContractData'));b=w['name'];s=w['sheet']
    values=[[0,16,32,48,64],[1,3,2,4,3],[0.1,0.2,0.1,0.2,0.15],[20,30,25,45,40],[1,2,1,2,1.5]]
    set_worksheet_data(b,s,json.dumps(values))
    before=json.loads(get_worksheet_data(b,s))
    item={'data_book':b,'data_sheet':s,'x_col':1,'y_col':2,'plot_type':'line+symbol','error_col':3,'error_kind':'SD'}
    plain={k:v for k,v in item.items() if k not in {'error_col','error_kind'}}
    mapping={'series':[item]}
    outputs={'runtime':{'origin_version_numeric':owned_origin.LTVar('@V')}}
    for family in ['single_axis','dual_y','inset','grouped_stacked_column']:
        data=dict(mapping);layout={}
        if family=='dual_y':data['right']=plain|{'y_col':4,'error_col':5,'error_kind':'SEM'}
        if family=='inset':layout={'inset':{'axes':{'x':{'from':16,'to':48},'y':{'from':1,'to':4}},'geometry':{'units':'parent_percent','left':45,'top':5,'width':45,'height':40}}}
        if family=='grouped_stacked_column':data={'series':[plain|{'plot_type':'column','y_cols':[2,3,4]}]}
        graph={'single_axis':'Csingle','dual_y':'Cdual','inset':'Cinset','grouped_stacked_column':'Cstack'}[family]
        first=json.loads(apply_figure_template(graph,family,json.dumps(data),json.dumps(layout),json.dumps({'font':'Arial','title_size_pt':24,'tick_size_pt':18})))
        second=json.loads(apply_figure_template(graph,family,json.dumps(data),json.dumps(layout)))
        assert first['created'] and not second['created']
        assert len(first['inspection']['layers'])==len(second['inspection']['layers'])
        assert [len(l['plots']) for l in first['inspection']['layers']]==[len(l['plots']) for l in second['inspection']['layers']]
        assert all(not x['not_applied'] for x in first['verification'])
        if family=='dual_y':
            right_plots=second['inspection']['layers'][1]['plots']
            assert [p['is_error'] for p in right_plots]==[False,True]
            assert [p['source_binding'] for p in right_plots]==[b+'_D',b+'_E']
            assert right_plots[1]['plot_type']==231
        outputs[family]=second
        if family=='grouped_stacked_column':
            with pytest.raises(ValueError,match='stacking'):
                ungroup_plots(graph)
            outputs['group_mode']=json.loads(set_group_edit_mode(graph))
            assert not outputs['group_mode']['not_applied']
        export_graph(graph,str(tmp_path/(family+'.svg')),'svg',powerpoint_compatible=True)
        export_graph(graph,str(tmp_path/(family+'.pdf')),'pdf')
        export_graph(graph,str(tmp_path/(family+'.png')),'png',width=1200)

    g='Cdual'
    profiled=json.loads(apply_figure_template('Csingle','single_axis',json.dumps(mapping),style=json.dumps({'profile':'large_source_arial'})))
    assert all(not r['not_applied'] and not r['unverifiable'] for r in profiled['verification'])
    assert profiled['inspection']['layers'][0]['axes']['y']['title_size_pt']==32
    assert profiled['inspection']['layers'][0]['axes']['y']['label.pt']==28
    checked=json.loads(configure_layer(g,json.dumps({'axes':{'x':{'from':0,'to':64,'inc':16}}}),layer_index=1))
    assert not checked['not_applied']
    set_plot_style(g,line_width=3,layer_id='mcpRightY')
    with layer_scope(g,layer_id='mcpRightY'):
        assert read_plot_option(g,'ContractData_D','wp')==3
    set_graph_font(g,font_size=24,target='axes',layer_id='mcpRightY',axis_side='right')
    right=json.loads(configure_layer(g,json.dumps({'axes':{'y2':{'title_bold':True,'title':'Temperature (K)','title_font':'Arial','title_size_pt':24,'tick_font':'Arial','tick_size_pt':18}}}),layer_id='mcpRightY'))
    assert not right['not_applied'] and not right['unverifiable']
    top_before=json.loads(inspect_graph(g))['layers'][0]['axes']['x2']['title']
    axis(g,op='labels',axis='top',label='Distance (um)',layer_id='mcpRightY')
    after_top=json.loads(inspect_graph(g))
    assert after_top['layers'][0]['axes']['x2']['title']==top_before
    assert after_top['layers'][1]['axes']['x2']['title']=='Distance (um)'
    assert after_top['layers'][1]['axes']['y2']['title']==r'\b(Temperature (K))'
    for area in ['layer','plot_area']:
        result=json.loads(export_graph(g,str(tmp_path/(area+'.svg')),'svg',area=area,layer_id='mcpRightY',powerpoint_compatible=True))
        assert Path(result['native_export']).is_file()
        root=parse_svg((tmp_path/(area+'.svg')).read_bytes())
        assert not list(root.iter('{http://www.w3.org/2000/svg}mask'))
        if area=='plot_area':
            assert not list(root.iter('{http://www.w3.org/2000/svg}text'))
        export_graph(g,str(tmp_path/(area+'.pdf')),'pdf',area=area,layer_id='mcpRightY')
        pdf=fitz.open(tmp_path/(area+'.pdf'))
        assert not pdf[0].get_images()  # The chart stays vector.
        pdf[0].get_pixmap(matrix=fitz.Matrix(1,1)).save(tmp_path/(area+'_preview.png'))
        pdf.close()
    m=json.loads(create_matrix('ContractMatrix',6,6,x_min=1,x_max=6,y_min=1,y_max=6))
    assert all(c['status']=='verified' for c in m['coordinate_verification'].values())
    grid=[[float(r+c+1) for c in range(6)] for r in range(6)]
    set_matrix_data(m['name'],json.dumps(grid)); matrix_before=get_matrix_data(m['name'])
    signed=json.loads(create_matrix('ContractSigned',6,6,x_min=1,x_max=6,y_min=1,y_max=6))
    set_matrix_data(signed['name'],json.dumps([[r+c-5.5 for c in range(6)] for r in range(6)]))
    signed_before=get_matrix_data(signed['name'])
    for scale in ['linear','log10','zero_centered']:
        map_m={'matrix_book':signed['name'] if scale=='zero_centered' else m['name'],'coordinates':{'x_min':1,'x_max':6,'y_min':1,'y_max':6}}
        lay={'color_scale':scale,'z_min':-12 if scale=='zero_centered' else 1,'z_max':12,'levels':12}
        name='ContractHeat'+scale.replace('_','')
        result=json.loads(apply_figure_template(name,'heatmap',json.dumps(map_m),json.dumps(lay)))
        repeat=json.loads(apply_figure_template(name,'heatmap',json.dumps(map_m),json.dumps(lay)))
        assert not repeat['created']
        outputs[scale]=result
        export_graph(name,str(tmp_path/(scale+'.svg')),'svg',powerpoint_compatible=True)
        export_graph(name,str(tmp_path/(scale+'.png')),'png',width=1000)
    assert get_matrix_data(m['name'])==matrix_before
    assert get_matrix_data(signed['name'])==signed_before
    assert json.loads(get_worksheet_data(b,s))==before
    # Save/reload only our own temporary project in the owned instance.
    project=tmp_path/'synthetic.opju'
    assert owned_origin.Save(str(project))
    assert owned_origin.Load(str(project))
    set_plot_style(g,line_width=2,layer_id='mcpRightY')
    loaded=json.loads(inspect_graph(g))
    assert loaded['layers'][0]['axes']['x']['inc']==16
    assert loaded['layers'][1]['plots'][0]['line_width_pt']==2
    outputs['loaded']=loaded
    assert loaded['layers'][1]['plots'][0]['x_binding']=='ContractData_A'
    # Duplicate Y datasets with different X bindings remain independently styled.
    from origin_pro_mcp.tools.graph import create_graph,add_plot_to_graph
    create_graph('Cduplicates',b,s,1,2,'line')
    add_plot_to_graph('Cduplicates',b,s,4,2,'line')
    set_plot_style('Cduplicates',1,line_width=1.5)
    set_plot_style('Cduplicates',2,line_width=4)
    duplicate=json.loads(inspect_graph('Cduplicates'))
    assert [p['line_width_pt'] for p in duplicate['layers'][0]['plots']]==[1.5,4]
    assert [p['x_binding'] for p in duplicate['layers'][0]['plots']]==['ContractData_A','ContractData_D']
    outputs['duplicates']=duplicate
    # Attach errors to an existing second-layer curve without touching layer 1.
    from origin_pro_mcp.tools.graph import add_second_y_axis,set_error_bars
    create_graph('Cattach',b,s,1,2,'line')
    add_second_y_axis('Cattach',b,s,1,4,'line')
    attach_before=json.loads(inspect_graph('Cattach'))
    set_error_bars('Cattach',b,s,4,5,layer_id='mcpRightY')
    attached=json.loads(inspect_graph('Cattach'))
    for old,new in zip(attach_before['layers'],attached['layers']):
        assert old['legend']['show']==new['legend']['show']
        for axis_name in ['x','y']:
            for prop in ['from','to','inc','rescale']:
                assert old['axes'][axis_name][prop]==new['axes'][axis_name][prop]
    assert len(attached['layers'][0]['plots'])==1
    assert [p['is_error'] for p in attached['layers'][1]['plots']]==[False,True]
    assert attached['layers'][1]['plots'][1]['plot_type']==231
    export_graph('Cattach',str(tmp_path/'attached_errors.png'),'png',width=1200)
    outputs['attached_errors']=attached
    assert owned_origin.Save(str(project))
    (tmp_path/'verification.json').write_text(json.dumps(outputs,indent=2),encoding='utf-8')
