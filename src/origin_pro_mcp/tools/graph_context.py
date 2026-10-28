"""Explicit layer selection and honest property diagnostics shared by tools."""
import contextlib
import contextvars
import functools
import inspect
import math

from ..labtalk_safe import labtalk_name, positive_int
from ..origin_connection import activate_window, execute_labtalk, get_origin

_target = contextvars.ContextVar('origin_graph_layer', default=None)
MISSING = -1.23456789e-300


def numeric(value):
    """Normalize unreadable Origin values; never confuse missing with zero."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or abs(value) >= 1e100:
        return None
    if value and math.isclose(value, MISSING, rel_tol=1e-6, abs_tol=0):
        return None
    return value


def finite(value, field, *, positive=False):
    if isinstance(value, bool):
        raise ValueError(f'{field} must be a finite number.')
    out = numeric(value)
    if out is None or (positive and out <= 0):
        raise ValueError(f'{field} must be finite' + (' and positive.' if positive else '.'))
    return out


def selected_index(graph_name):
    target = _target.get()
    return target[1] if target and target[0] == graph_name else 1


def explicit_selection(graph_name):
    target = _target.get()
    return bool(target and target[0] == graph_name and target[2])


def graph_page(graph_name):
    o = get_origin()
    pages = o.GraphPages
    for i in range(pages.Count):
        p = pages.Item(i)
        if p.Name == graph_name:
            return p
    raise ValueError(f'Graph {graph_name!r} not found.')


def resolve_layer(graph_name, layer_index=None, layer_id=None):
    """Resolve a persistent Origin layer name, checking ambiguous selectors."""
    graph_name = labtalk_name(graph_name, 'graph_name')
    if layer_index is None and not layer_id:
        return selected_index(graph_name)
    if layer_index is not None and (isinstance(layer_index,bool) or not isinstance(layer_index,int)):
        raise ValueError('layer_index must be a positive integer.')
    activate_window(graph_name, 'graph_name')
    layers = graph_page(graph_name).Layers
    index = positive_int(layer_index, 'layer_index') if layer_index is not None else None
    if layer_id:
        layer_id = labtalk_name(layer_id, 'layer_id')
        matches = [i + 1 for i in range(layers.Count) if layers.Item(i).Name == layer_id]
        if len(matches) != 1:
            raise ValueError(f'layer_id {layer_id!r} must identify exactly one layer on {graph_name}.')
        if index is not None and index != matches[0]:
            raise ValueError('layer_index and layer_id identify different layers.')
        index = matches[0]
    if index < 1 or index > layers.Count:
        raise ValueError(f'layer_index {index} is out of range 1..{layers.Count}.')
    return index


@contextlib.contextmanager
def layer_scope(graph_name, layer_index=None, layer_id=None):
    index = resolve_layer(graph_name, layer_index, layer_id)
    token = _target.set((graph_name, index, layer_index is not None or bool(layer_id)))
    try:
        yield index
    finally:
        _target.reset(token)


def layer_targeted(fn):
    """Keep the public typed signature while binding nested legacy helpers."""
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        graph = bound.arguments['graph_name']
        with layer_scope(graph, bound.arguments.get('layer_index'), bound.arguments.get('layer_id')):
            return fn(*args, **kwargs)
    return wrapped


def read_number(graph_name, expression, layer_index=None):
    from .style_helpers import acquire_graph_layer
    with layer_scope(graph_name, layer_index):
        gl = acquire_graph_layer(graph_name)
        # A NaN initialization distinguishes an unsupported get/property from zero.
        if not gl.Execute(f'__mcp_value=0/0; __mcp_value={expression};'):
            return None
        try:
            return numeric(get_origin().LTVar('__mcp_value'))
        except Exception:
            return None


def read_string(graph_name, expression, layer_index=None):
    from .style_helpers import acquire_graph_layer
    with layer_scope(graph_name, layer_index):
        gl = acquire_graph_layer(graph_name)
        if not gl.Execute(f'string __mcp_text$; __mcp_text$=""; __mcp_text$={expression};'):
            return None
        try:
            return get_origin().LTStr('__mcp_text$') or None
        except Exception:
            return None


def read_plot_option(graph_name, dataset, option):
    from .style_helpers import graph_layer_execute
    prefix=''
    if dataset.startswith('['):
        prefix=f'range __mcp_plot={dataset}; '
        dataset='__mcp_plot'
    if not graph_layer_execute(graph_name, f'{prefix}__mcp_option=0/0; get {dataset} -{option} __mcp_option;'):
        return None
    try:
        return numeric(get_origin().LTVar('__mcp_option'))
    except Exception:
        return None


def read_plot_x_binding(graph_name, layer_index, plot_position):
    """Resolve the actual plotted X column, including repeated Y datasets."""
    from .style_helpers import acquire_graph_layer
    with layer_scope(graph_name,layer_index):
        gl=acquire_graph_layer(graph_name)
        script=('string __mcp_xname$=""; '
                f'range -wx __mcp_xsource=[{graph_name}]{layer_index}!{plot_position}; '
                '__mcp_xname$=nameof(__mcp_xsource)$;')
        if not gl.Execute(script):
            return None
        return get_origin().LTStr('__mcp_xname$') or None
def check(requested, actual, *, tolerance=1e-6):
    if actual is None:
        status = 'unverifiable'
    elif isinstance(requested, (int, float)) and not isinstance(requested, bool):
        status = 'verified' if math.isclose(requested, actual, rel_tol=tolerance, abs_tol=tolerance) else 'not_applied'
    else:
        status = 'verified' if requested == actual else 'not_applied'
    return {'requested': requested, 'actual': actual, 'status': status}


def report(graph_name, layer_index, properties):
    return {
        'graph_name': graph_name, 'layer_index': layer_index,
        'properties': properties,
        'not_applied': [k for k, v in properties.items() if v['status'] == 'not_applied'],
        'unverifiable': [k for k, v in properties.items() if v['status'] == 'unverifiable'],
        'verification_scope': 'Origin property readback; exported appearance must be inspected separately',
    }


def set_width_points(graph_name, dataset, points):
    """Prefer the physical-unit flag, fall back only after measured capability."""
    from .style_helpers import graph_layer_execute
    points = finite(points, 'line_width', positive=True)
    # get -wp probes support without mutating an unrelated user object.
    before_pt = read_plot_option(graph_name, dataset, 'wp')
    before_raw = read_plot_option(graph_name, dataset, 'w')
    prefix=''
    if dataset.startswith('['):
        prefix=f'range __mcp_plot={dataset}; '
        target='__mcp_plot'
    else:
        target=dataset
    if before_pt is not None and before_pt >= 0:
        if not graph_layer_execute(graph_name, f'{prefix}set {target} -wp {points:g};'):
            raise ValueError('Origin rejected the point-unit line width.')
        actual = read_plot_option(graph_name, dataset, 'wp')
        return check(points, actual), 'wp'
    # Legacy builds may lack -wp. A get -w uses the documented 500 units/pt;
    # confirm the write by readback. Do not retain the erroneous 200 factor.
    if before_raw is not None and before_raw >= 0:
        if not graph_layer_execute(graph_name, f'{prefix}set {target} -w {points * 500:g};'):
            raise ValueError('Origin rejected the legacy line width.')
        raw = read_plot_option(graph_name, dataset, 'w')
        return check(points, None if raw is None else raw / 500), 'w_500_documented'
    raise ValueError('Neither get -wp nor get -w is readable; line width capability is unverified. No width was changed.')
