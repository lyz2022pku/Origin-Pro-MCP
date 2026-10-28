"""Run only inside an explicitly owned Origin session with existing data.

This example supplies mappings and styling separately. It creates no statistics
and contains no study-specific paths. Replace the workbook/sheet names with
explicit inputs; never implicitly attach to a user's open project.
"""
import json

from origin_pro_mcp.tools.figure_templates import apply_figure_template
from origin_pro_mcp.tools.graph import export_graph
from origin_pro_mcp.tools.graph_edit import configure_layer, inspect_graph


def dual_y(graph_name, data_book, data_sheet, output_svg):
    mapping={
        'series':[{'data_book':data_book,'data_sheet':data_sheet,'x_col':1,'y_col':2}],
        'right':{'data_book':data_book,'data_sheet':data_sheet,'x_col':1,'y_col':3},
    }
    result=apply_figure_template(graph_name,'dual_y',json.dumps(mapping))
    verification=configure_layer(graph_name,json.dumps({
        'axes':{'y2':{'title':'Temperature (K)','title_size_pt':24,'tick_size_pt':20}},
    }),layer_id='mcpRightY')
    export=export_graph(graph_name,output_svg,'svg',powerpoint_compatible=True)
    return {'template':json.loads(result),'edit':json.loads(verification),
            'inspection':json.loads(inspect_graph(graph_name)),'export':json.loads(export)}
