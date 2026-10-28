"""Shared fake Origin COM doubles.

Importable from both ``conftest.py`` (for the ``fake_origin`` fixture) and the
daemon/transport tests, which spin up several independent ``FakeOrigin``
instances on separate worker threads. Keeping the fakes here (instead of inside
``conftest``) lets every test module import the exact same COM surface.
"""

import re


class FakePages:
    def __init__(self, pages):
        self._pages = pages

    @property
    def Count(self):
        return len(self._pages)

    def Item(self, i):
        return self._pages[i]


class FakeColumn:
    def __init__(self, name, col_type=0, long_name=""):
        self.Name = name
        self.Type = col_type  # COM designation: 0=Y, 2=Y Error, 3=X
        self.LongName = long_name


class FakeSheet:
    def __init__(self, name, columns=()):
        self.Name = name
        self.columns = list(columns)

    @property
    def Columns(self):
        return FakePages(self.columns)


class FakeBook:
    def __init__(self, name, sheets=("Sheet1",)):
        self.Name = name
        self.sheets = [
            s if isinstance(s, FakeSheet) else FakeSheet(s) for s in sheets
        ]

    @property
    def Layers(self):
        return FakePages(self.sheets)


class FakePlot:
    def __init__(self, name, graph=None, index=None):
        self.Name = name
        self.activated = False
        self._graph = graph
        self._index = index

    def Activate(self):
        self.activated = True

    def Destroy(self):
        # Model the real DataPlot.Destroy(): remove ONLY this plot (by its
        # position in the collection), so duplicate-dataset plots are removed
        # one at a time rather than all-by-name.
        if self._graph is not None and self._index is not None:
            del self._graph.plot_names[self._index]


# Match `layer.<x|y|x2|y2>.<from|to|thickness> = <number>` writes and
# `__var = layer.<x|y|x2|y2>.<from|to|thickness>` reads so the fake can model
# the loaded-graph freeze (writes ignored) and the read-back verification path
# (verify_layer_value).
_LAYER_WRITE_RE = re.compile(r"layer\.([xy]2?)\.(from|to|thickness)\s*=\s*(-?[0-9.eE+]+)")
_LAYER_READ_RE = re.compile(r"^\s*(__\w+)\s*=\s*layer\.([xy]2?)\.(from|to|thickness)\s*;?\s*$")
# Panel geometry (layer.left/top/width/height) writes/reads, so the fake can
# model set_layer_geometry's read-back verification the same way.
_LAYER_GEO_WRITE_RE = re.compile(r"layer\.(left|top|width|height|unit|link|ResizeParent)\s*=\s*(-?[0-9.eE+]+)")
_LAYER_GEO_READ_RE = re.compile(r"^\s*(__\w+)\s*=\s*layer\.(left|top|width|height|unit|link|ResizeParent)\s*;?\s*$")


class FakeLayer:
    """A graph layer COM double.

    ``DataPlots`` is computed from the parent graph's *visible* plots, so a
    graph flagged ``loaded`` reports zero plots until its page is activated and
    ``frozen`` reports zero forever — modelling the .opju freeze. ``Execute``
    delegates recording and result to the parent FakeOrigin (so tests can still
    assert on ``fake_origin.executed`` and inject failures via
    ``execute_results``) and models ``layer.<axis>.from/to`` writes/reads for
    the read-back verification path.
    """

    def __init__(self, graph, origin=None):
        self._graph = graph
        self._origin = origin
        self.executed = []
        self._vals = {}
        self._options = {}
        self.Name = 'Layer1'

    @property
    def DataPlots(self):
        names = self._graph.visible_plot_names() if self._graph is not None else []
        return FakePages([FakePlot(n, self._graph, i) for i, n in enumerate(names)])

    def Execute(self, script):
        self.executed.append(script)
        o = self._origin
        name_set = re.search(r'layer.name\$\s*=\s*"([A-Za-z_]\w*)";',script)
        if name_set:
            self.Name=name_set[1]
        plot = re.search(r'plotxy iy:=\[([^]]+)\]([^!]+)!\((?P<x>\d+),(?P<y>\d+)(?:,(?P<error>\d+))?\)',script)
        if plot:
            self._graph.plot_names.append(plot[1]+'_'+chr(64+int(plot['y'])))
            self._x_binding=plot[1]+'_'+chr(64+int(plot['x']))
            if plot['error']:
                self._graph.plot_names.append(plot[1]+'_'+chr(64+int(plot['error'])))
        if 'range -wx __mcp_xsource=' in script and o is not None:
            o.__dict__.setdefault('_strings', {})['__mcp_xname$']=getattr(self, '_x_binding', '')
        option_set = re.search(r'set (\S+) -(wp|w) ([0-9.eE+-]+);',script)
        option_get = re.search(r'get (\S+) -(wp|w) (__\w+);',script)
        if option_set and not self._graph.frozen:
            ds,flag,value=option_set.groups()
            self._options[ds]=float(value)/(500 if flag=='w' else 1)
        if option_get and o is not None:
            ds,flag,var=option_get.groups()
            o.lt_vars[var]=self._options.get(ds,1.0)*(500 if flag=='w' else 1)
        if script.startswith('__mcp_value=0/0;') and o is not None:
            expr=script.split('__mcp_value=',2)[-1].rstrip(';')
            match=re.fullmatch(r'layer.plot(\d+).pid',expr)
            if match:
                index=int(match[1])-1
                names=self._graph.visible_plot_names()
                name=names[index] if index<len(names) else ''
                error=any(name==b.Name+'_'+c.Name and c.Type==2 for b in o.books for sheet in b.sheets for c in sheet.columns)
                o.lt_vars['__mcp_value']=231.0 if error else 200.0
            else:
                o.lt_vars['__mcp_value']=self._vals.get(('geo',expr.removeprefix('layer.')),0.0)
        read = _LAYER_READ_RE.match(script)
        geo_read = _LAYER_GEO_READ_RE.match(script)
        if read is not None and o is not None:
            var, axis, bound = read.group(1), read.group(2), read.group(3)
            o.lt_vars[var] = self._vals.get((axis, bound), 0.0)
        elif geo_read is not None and o is not None:
            var, prop = geo_read.group(1), geo_read.group(2)
            o.lt_vars[var] = self._vals.get(("geo", prop), 0.0)
        else:
            frozen = self._graph is not None and self._graph.frozen
            for w in _LAYER_WRITE_RE.finditer(script):
                if not frozen:  # a frozen layer silently ignores the write
                    self._vals[(w.group(1), w.group(2))] = float(w.group(3))
            for w in _LAYER_GEO_WRITE_RE.finditer(script):
                if not frozen:
                    self._vals[("geo", w.group(1))] = float(w.group(2))
        if o is None:
            return True
        o.executed.append(script)
        for prefix, result in o.execute_results.items():
            if script.startswith(prefix):
                return result
        return True


class FakeGraph:
    def __init__(self, name, plot_names=(), *, loaded=False, frozen=False):
        self.Name = name
        self.plot_names = list(plot_names)
        # loaded: DataPlots empty until the page is activated (win -a).
        # frozen: DataPlots empty even after activation (unrecoverable freeze).
        self.loaded = loaded
        self.frozen = frozen
        self.activated = False

    def visible_plot_names(self):
        if self.frozen:
            return []
        if self.loaded and not self.activated:
            return []
        return self.plot_names

    @property
    def Layers(self):
        # Fallback acquisition route (_find_layer_com via GraphPages).
        if not hasattr(self,'_layers'):
            self._layers=[FakeLayer(self,getattr(self,'_origin',None))]
        return FakePages(self._layers)


class FakeMatrix:
    def __init__(self, name):
        self.Name = name


class FakeOrigin:
    """Mimics the Origin COM surface the tools rely on."""

    def __init__(self):
        self.books = [FakeBook("Book1")]
        self.graphs = [FakeGraph("Graph1")]
        self.execute_results = {}
        self.executed = []
        self.save_result = True
        self.load_result = True
        self.saved_paths = []
        self.put_result = True
        self.worksheet_data = ((1.0, 4.0), (2.0, 5.0))
        self.matrices = []
        self.matrix_data = {}
        self.lt_vars = {}
        # When True, FindGraphLayer returns None so acquisition must use the
        # GraphPages fallback route (exercises _find_layer_com's second path).
        self.find_graph_layer_returns_none = False

    @property
    def WorksheetPages(self):
        return FakePages(self.books)

    @property
    def GraphPages(self):
        for g in self.graphs:
            g._origin=self
            for i in range(g.Layers.Count):
                g.Layers.Item(i)._origin=self
        return FakePages(self.graphs)

    @property
    def MatrixPages(self):
        return FakePages(self.matrices)

    def Execute(self, script):
        self.executed.append(script)
        self._maybe_activate(script)
        active=re.search(r'page.active\s*=\s*(\d+)',script)
        if active:self.__dict__['_active_layer']=int(active[1])
        if script.startswith('layadd'):
            graph=next(g for g in self.graphs if g.Name==getattr(self,'_active_graph',g.Name))
            self.GraphPages
            child=FakeGraph(graph.Name)
            layer=FakeLayer(child,self);layer.Name='RightY'
            layer._vals.update({('geo','unit'):7,('geo','link'):1,('geo','width'):100,('geo','height'):100})
            graph._layers.append(layer)
            self._active_layer=graph.Layers.Count
        if 'nameof(__mcp_src)$' in script:
            m=re.search(r'\[([^]]+)\][^!]+!col\((\d+)\)',script)
            if m:self.__dict__.setdefault('_strings',{})['__mcp_ds$']=m[1]+'_'+chr(64+int(m[2]))
        if script.startswith('plotxy'):
            m=re.search(r'ogl:=\[([^]]+)\](\d+)!',script)
            if m:
                g=next(g for g in self.graphs if g.Name==m[1])
                # The real global plotxy targets the explicit destination layer.
                g.Layers.Item(int(m[2])-1).Execute(script)
        for prefix, result in self.execute_results.items():
            if script.startswith(prefix):
                return result
        return True

    def _maybe_activate(self, script):
        # Model `win -a <name>` activating a graph page: a graph flagged
        # ``loaded`` only reveals its DataPlots once its page is active.
        m = re.match(r"\s*win\s+-a\s+([A-Za-z_]\w*)", script)
        if m is None:
            return
        name = m.group(1)
        self._active_graph=name
        for g in self.graphs:
            if g.Name == name:
                g.activated = True

    def FindWorksheet(self, target):
        for book in self.books:
            for j in range(book.Layers.Count):
                sheet = book.Layers.Item(j)
                if target == f"[{book.Name}]{sheet.Name}":
                    return sheet
        return None

    def FindGraphLayer(self, target):
        if self.find_graph_layer_returns_none:
            return None
        cache = self.__dict__.setdefault("_graph_layers", {})
        if target in cache:
            return cache[target]
        for graph in self.graphs:
            if target == f"[{graph.Name}]Layer1":
                graph._origin=self
                cache[target] = graph.Layers.Item(0)
                cache[target]._origin=self
                return cache[target]
        return None

    def FindMatrixSheet(self, target):
        for m in self.matrices:
            if target in (m.Name, f"[{m.Name}]MSheet1"):
                return m
        return None

    def PutMatrix(self, target, data):
        self.matrix_data[target] = [list(r) for r in data]
        return self.put_result

    def GetMatrix(self, target):
        if self.FindMatrixSheet(target) is None:
            return -2147352568
        grid = self.matrix_data.get(target, ((1.0, 2.0), (3.0, 4.0)))
        return tuple(tuple(r) for r in grid)

    def GetWorksheet(self, target):
        if self.FindWorksheet(target) is None:
            return -2147352568  # HRESULT int, as observed on Origin 2020
        return self.worksheet_data

    def PutWorksheet(self, target, data, row, col):
        return self.put_result

    def Save(self, path):
        self.saved_paths.append(path)
        return self.save_result

    def Load(self, path):
        return self.load_result

    def CreatePage(self, kind, name, template):
        return name

    def LTVar(self, name):
        if name in self.lt_vars:return self.lt_vars[name]
        if name=='page.active':return getattr(self,'_active_layer',1)
        if name=='page.nlayers':
            return next((g.Layers.Count for g in self.graphs if g.Name==getattr(self,'_active_graph','')),0)
        return self.lt_vars.get(name, 0.0)

    def LTStr(self, name):
        return getattr(self,'_strings',{}).get(name,'')


class ThreadGuardedFake:
    """A COM-proxy stand-in that records ANY off-owner-thread attribute access.

    The watchdog and the heartbeat/idle monitor must NEVER dereference a COM
    proxy — they only ever deal in PIDs. We hand those threads the int pid (never
    this object) and assert ``touched`` stays False, proving they stayed
    COM-free. Defining no real methods/attributes here is deliberate: every
    access funnels through ``__getattr__`` so the owning-thread guard fires on
    each one (a real method would bypass ``__getattr__`` and defeat detection).
    """

    def __init__(self, owner_thread_id: int):
        object.__setattr__(self, "_owner", owner_thread_id)
        object.__setattr__(self, "touched", False)

    def __getattr__(self, name):
        import threading

        if name in ("_owner", "touched"):
            return object.__getattribute__(self, name)
        if threading.get_ident() != object.__getattribute__(self, "_owner"):
            object.__setattr__(self, "touched", True)
            raise RuntimeError(f"COM proxy touched off-owner-thread: {name!r}")
        return object.__getattribute__(self, name)
