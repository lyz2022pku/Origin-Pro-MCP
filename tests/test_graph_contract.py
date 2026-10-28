"""Unit tests for layer identity, physical units and conservative exports."""
import json
import math
import xml.etree.ElementTree as ET

import pytest

from origin_pro_mcp.tools import graph_context as context
from origin_pro_mcp.tools.vector_export import parse_svg,convert_rectangular_masks,crop_layer


@pytest.mark.parametrize('value',[None,float('nan'),float('inf'),-1.23456789e-300,1.2e308])
def test_missing_readback_is_unverifiable(value):
    assert context.numeric(value) is None
    assert context.check(3,context.numeric(value))['status']=='unverifiable'


def test_zero_readback_is_valid():
    assert context.numeric(0)==0
    assert context.check(0,0)['status']=='verified'
    assert context.check(3,0)['status']=='not_applied'


def test_width_fallback_uses_documented_500_not_200(monkeypatch):
    from origin_pro_mcp.tools import style_helpers
    writes=[];state={'raw':500}
    def read(_graph,_dataset,flag):
        return None if flag=='wp' else state['raw']
    def write(_graph,command):
        writes.append(command);state['raw']=float(command.split()[-1].rstrip(';'));return True
    monkeypatch.setattr(context,'read_plot_option',read)
    monkeypatch.setattr(style_helpers,'graph_layer_execute',write)
    result,method=context.set_width_points('G','Data_B',3)
    assert writes==['set Data_B -w 1500;']
    assert method=='w_500_documented' and result['status']=='verified'


def test_unreadable_width_never_mutates(monkeypatch):
    from origin_pro_mcp.tools import style_helpers
    monkeypatch.setattr(context,'read_plot_option',lambda *a:None)
    monkeypatch.setattr(style_helpers,'graph_layer_execute',lambda *a:pytest.fail('unverified write'))
    with pytest.raises(ValueError,match='No width was changed'):
        context.set_width_points('G','D',3)


@pytest.mark.parametrize('value',[-1,0,float('nan'),float('inf'),True])
def test_bad_physical_width_rejected_before_write(value):
    with pytest.raises(ValueError):context.set_width_points('G','D',value)


def test_layer_identity_survives_reorder_and_conflicts_fail(fake_origin):
    from fakes import FakeLayer,FakeGraph
    from origin_pro_mcp.tools.style_helpers import acquire_graph_layer
    g=fake_origin.graphs[0]
    fake_origin.GraphPages
    child=FakeLayer(FakeGraph(g.Name),fake_origin);child.Name='temperature'
    g._layers.append(child)
    assert context.resolve_layer(g.Name,layer_id='temperature')==2
    with context.layer_scope(g.Name,layer_id='temperature'):
        assert acquire_graph_layer(g.Name) is child
    with pytest.raises(ValueError,match='different layers'):
        context.resolve_layer(g.Name,1,'temperature')
    g._layers.reverse()
    assert context.resolve_layer(g.Name,layer_id='temperature')==1
    with context.layer_scope(g.Name,layer_id='temperature'):
        assert acquire_graph_layer(g.Name) is child


def test_sidecar_atomic_replace_retries_transient_windows_reader(monkeypatch,tmp_path):
    from origin_pro_mcp import daemon
    real=daemon.os.replace;calls=[]
    def busy_once(source,destination):
        calls.append(destination)
        if len(calls)==1:raise PermissionError('reader denies delete sharing')
        return real(source,destination)
    monkeypatch.setattr(daemon.os,'replace',busy_once)
    monkeypatch.setattr(daemon.time,'sleep',lambda _:None)
    path=tmp_path/'sessions.json'
    daemon.write_sessions(str(path),{'generation':'test','sessions':{}})
    assert len(calls)==2 and path.is_file()


def test_dual_y_repeats_preserve_existing_state_and_legend(fake_origin):
    from origin_pro_mcp.tools.graph import add_second_y_axis
    from origin_pro_mcp.tools.style_helpers import acquire_graph_layer
    main=acquire_graph_layer('Graph1')
    main._vals.update({('geo','left'):20,('geo','width'):60,('x','from'):0,('x','to'):64})
    add_second_y_axis('Graph1','Book1','Sheet1',1,2)
    count=fake_origin.graphs[0].Layers.Count
    add_second_y_axis('Graph1','Book1','Sheet1',1,2)
    assert fake_origin.graphs[0].Layers.Count==count==2
    assert fake_origin.graphs[0].Layers.Item(1).DataPlots.Count==1
    assert main._vals[('geo','left')]==20 and main._vals[('geo','width')]==60
    assert not any('legend -r' in s for s in fake_origin.executed)


def test_layer_patch_validates_every_field_before_mutation(fake_origin):
    from origin_pro_mcp.tools.graph_edit import configure_layer
    with pytest.raises(ValueError,match='Unknown axis'):
        configure_layer('Graph1',json.dumps({'axes':{'x':{'from':5},'y':{'unknown':2}}}))
    assert not any('layer.x.from=' in s for s in fake_origin.executed)


def test_inspection_has_explicit_missing_and_physical_page_units(fake_origin):
    from origin_pro_mcp.tools.graph_edit import inspect_graph
    result=json.loads(inspect_graph('Graph1'))
    assert result['schema_version']==1
    assert result['page']['width_mm'] is None
    assert result['layers'][0]['axes']['x']['title'] is None


def svg(mask='<mask id="cut"><rect x="10" y="20" width="40" height="30" fill="white"/></mask>'):
    return parse_svg(f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:olab="http://www.originlab.com/namespaces/olab" viewBox="0 0 100 100" width="100pt" height="100pt"><defs>{mask}</defs><g mask="url(#cut)"><polyline olab:scope="Layer0.Plot0" points="10,20 40,50"/></g><text olab:scope="Layer0.Axis0.Label">outside fragments</text><path olab:scope="Layer1.Plot0" d="M 0 0 L 2 2"/></svg>'''.encode())


def test_rectangular_opaque_mask_converts_structurally():
    root=svg();result=convert_rectangular_masks(root)
    assert result['converted_masks']==['cut'] and not result['unsupported_masks']
    assert list(root.iter('{http://www.w3.org/2000/svg}clipPath'))
    assert any(n.get('clip-path')=='url(#cut)' for n in root.iter())


@pytest.mark.parametrize('mask',[
    '<mask id="cut"><rect width="40" height="30" fill="black"/></mask>',
    '<mask id="cut"><rect width="40" height="30" fill="white" opacity="0.5"/></mask>',
    '<mask id="cut"><circle r="20" fill="white"/></mask>',
    '<mask id="cut" transform="scale(2)"><rect width="40" height="30" fill="white"/></mask>',
    '<mask id="cut" maskContentUnits="objectBoundingBox"><rect width="1" height="1" fill="white"/></mask>',
])
def test_unsupported_masks_are_preserved(mask):
    root=svg(mask);result=convert_rectangular_masks(root)
    assert result['unsupported_masks']==['cut']
    assert list(root.iter('{http://www.w3.org/2000/svg}mask'))


def test_plot_crop_removes_axis_objects_before_clipping():
    root=svg();crop_layer(root,1,True)
    assert root.get('viewBox')=='10 20 40 30'
    assert not list(root.iter('{http://www.w3.org/2000/svg}text'))
    scopes=[n.get('{http://www.originlab.com/namespaces/olab}scope') for n in root.iter()]
    assert 'Layer1.Plot0' not in scopes and 'Layer0.Plot0' in scopes


def test_unrecognized_semantic_crop_fails():
    with pytest.raises(ValueError,match='semantic'):
        crop_layer(parse_svg(b'<svg xmlns="http://www.w3.org/2000/svg"/>'),1,True)


def test_stale_export_never_counts_as_a_success(fake_origin,tmp_path):
    from origin_pro_mcp.tools.graph import export_graph
    path=tmp_path/'stale.png';path.write_bytes(b'old result')
    with pytest.raises(ValueError,match='was not created'):
        export_graph('Graph1',str(path))
    assert path.read_bytes()==b'old result'


def test_error_kind_is_required_before_any_template_creates_graph():
    from origin_pro_mcp.tools.figure_templates import validate_spec
    mapping={'series':[{'data_book':'D','data_sheet':'S','x_col':1,'y_col':2,'error_col':3}]}
    with pytest.raises(ValueError,match='error_kind'):
        validate_spec('single_axis',mapping,{}, {},1)


def test_profile_is_optional_and_does_not_change_default_style():
    from origin_pro_mcp.tools.figure_templates import validate_spec
    mapping={'series':[{'data_book':'D','data_sheet':'S','x_col':1,'y_col':2}]}
    assert validate_spec('single_axis',mapping,{}, {},1)=={}
    profile=validate_spec('single_axis',mapping,{}, {'profile':'large_source_arial'},1)
    assert profile['title_size_pt']==32 and profile['tick_size_pt']==28


def test_plot_style_diagnostics_remain_structured(fake_origin):
    from origin_pro_mcp.tools.style import set_plot_style
    from fakes import FakeGraph
    fake_origin.graphs=[FakeGraph('G',['Book1_B'])]
    result=json.loads(set_plot_style('G',line_width=3,diagnostics=True))
    assert result['properties']['line_width_pt']['requested']==3
    assert result['properties']['line_width_pt']['actual']==3
    assert not result['not_applied'] and not result['unverifiable']
