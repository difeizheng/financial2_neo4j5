"""ECharts HTML template for propagation graph animation."""
from __future__ import annotations

_ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"


def render_propagation_html(
    graph_json: str,
    height: str = "800px",
    echarts_cdn: str = _ECHARTS_CDN,
) -> str:
    """Return a complete HTML string embedding ECharts propagation graph.

    graph_json: JSON string from build_propagation_data(), injected as JS variable.
    """
    # part1: f-string for Python variables (height, echarts_cdn only).
    # Ends just before graph_json injection to avoid Python interpreting
    # JSON braces like {"lit": false} as f-string placeholders.
    part1 = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #0f1117; font-family: 'Segoe UI', sans-serif; overflow: hidden; }}
  #wrap {{ position: relative; width: 100%; height: {height}; }}
  #chart {{ width: 100%; height: 100%; }}
  #controls {{
    position: absolute; top: 12px; left: 12px; z-index: 10;
    background: rgba(20,24,36,0.92); border: 1px solid #2a3050;
    border-radius: 8px; padding: 10px 14px; color: #cdd6f4;
    display: flex; flex-direction: column; gap: 8px; min-width: 220px;
  }}
  .ctrl-row {{ display: flex; align-items: center; gap: 8px; }}
  .ctrl-label {{ font-size: 12px; color: #a6adc8; min-width: 70px; }}
  .ctrl-val {{ font-size: 12px; color: #89b4fa; min-width: 24px; text-align: right; }}
  input[type=range] {{ flex: 1; accent-color: #89b4fa; cursor: pointer; }}
  button {{
    padding: 5px 12px; border-radius: 5px; border: none; cursor: pointer;
    font-size: 12px; font-weight: 600; transition: opacity .15s;
  }}
  button:hover {{ opacity: 0.85; }}
  #btn-play {{ background: #a6e3a1; color: #1e1e2e; }}
  #btn-reset {{ background: #45475a; color: #cdd6f4; }}
  #btn-fs {{ background: #89b4fa; color: #1e1e2e; }}
  .btn-row {{ display: flex; gap: 6px; }}
  #stats {{
    position: absolute; bottom: 10px; left: 12px; z-index: 10;
    background: rgba(20,24,36,0.85); border-radius: 6px;
    padding: 5px 10px; font-size: 11px; color: #6c7086;
  }}
  #warn {{
    position: absolute; bottom: 10px; right: 12px; z-index: 10;
    background: rgba(250,179,135,0.15); border: 1px solid #fab387;
    border-radius: 6px; padding: 5px 10px; font-size: 11px; color: #fab387;
    display: none;
  }}
</style>
</head>
<body>
<div id="wrap">
  <div id="chart"></div>
  <div id="controls">
    <div class="btn-row">
      <button id="btn-play">&#9654; 播放</button>
      <button id="btn-reset">&#8634; 重置</button>
      <button id="btn-fs">&#x26F6; 全屏</button>
    </div>
    <div class="ctrl-row">
      <span class="ctrl-label">显示深度</span>
      <input type="range" id="sl-depth" min="1" max="20" value="20">
      <span class="ctrl-val" id="lbl-depth">20</span>
    </div>
    <div class="ctrl-row">
      <span class="ctrl-label">动画速度</span>
      <input type="range" id="sl-speed" min="1" max="5" value="3">
      <span class="ctrl-val" id="lbl-speed">3x</span>
    </div>
  </div>
  <div id="stats">节点: 0 | 边: 0</div>
  <div id="warn">&#9888; 图谱已截断</div>
</div>
<script src="{echarts_cdn}"></script>
<script>
var graphData = """

    # part2: raw string — no Python variables, no f-string escaping needed.
    part2 = r"""
;

var chart = echarts.init(document.getElementById('chart'), 'dark', {renderer: 'canvas'});
var allNodes = graphData.nodes;
var allEdges = graphData.edges;
var depthLevels = graphData.depth_levels;
var rootId = graphData.root_id;
var maxDataDepth = graphData.max_depth;
var stats = graphData.stats;

var SPEED_DELAYS = [2000, 1200, 700, 400, 200];
var animTimer = null;
var isPlaying = false;
var currentAnimDepth = 0;
var displayDepth = 20;

var nodeIndex = {};
allNodes.forEach(function(n, i) { nodeIndex[n.id] = i; });

function buildDisplayNodes(litSet, depthLimit) {
  return allNodes.map(function(n) {
    var hidden = n.depth > depthLimit;
    var isLit = litSet.has(n.id) || n.id === rootId;
    return Object.assign({}, n, {
      symbolSize: hidden ? 0 : (isLit ? n.symbolSize : 4),
      itemStyle: {
        opacity: hidden ? 0 : (isLit ? 1 : 0.15),
        color: hidden ? 'transparent' : undefined,
      },
      label: {show: isLit && !hidden},
    });
  });
}

function buildDisplayEdges(litSet, depthLimit) {
  return allEdges.map(function(e) {
    var srcNode = allNodes[nodeIndex[e.source]];
    var tgtNode = allNodes[nodeIndex[e.target]];
    var srcDepth = srcNode ? srcNode.depth : 0;
    var tgtDepth = tgtNode ? tgtNode.depth : 0;
    var hidden = srcDepth > depthLimit || tgtDepth > depthLimit;
    var isLit = litSet.has(e.target) || e.target === rootId;
    return Object.assign({}, e, {
      lineStyle: {
        opacity: hidden ? 0 : (isLit ? 0.8 : 0.08),
        width: isLit ? 1.5 : 0.8,
      },
    });
  });
}

var litNodes = new Set([rootId]);

function getOption(nodes, edges) {
  return {
    backgroundColor: '#0f1117',
    legend: {
      data: graphData.categories.map(function(c) { return c.name; }),
      textStyle: {color: '#a6adc8'},
      top: 8, right: 12,
    },
    tooltip: {
      trigger: 'item',
      formatter: function(params) {
        if (params.dataType !== 'node') return '';
        var d = params.data;
        var lines = [
          '<b>' + (d.id || '') + '</b>',
          'Sheet: ' + (d.sheet || ''),
          '深度: ' + (d.depth !== undefined ? d.depth : ''),
        ];
        if (d.value_old !== null && d.value_old !== undefined)
          lines.push('旧値: ' + d.value_old);
        if (d.value_new !== null && d.value_new !== undefined)
          lines.push('新値: ' + d.value_new);
        if (d.formula) lines.push('公式: ' + d.formula.substring(0, 60));
        if (d.indicator_name) lines.push('指标: ' + d.indicator_name);
        return lines.join('<br>');
      },
    },
    series: [{
      type: 'graph',
      layout: 'force',
      data: nodes,
      links: edges,
      categories: graphData.categories,
      roam: true,
      draggable: true,
      force: {
        repulsion: 120,
        gravity: 0.05,
        edgeLength: [40, 160],
        friction: 0.6,
        layoutAnimation: true,
      },
      edgeSymbol: ['none', 'arrow'],
      edgeSymbolSize: [0, 7],
      emphasis: {focus: 'adjacency', lineStyle: {width: 3}},
      animationDurationUpdate: 300,
      animationEasingUpdate: 'cubicInOut',
      label: {
        show: false,
        position: 'right',
        fontSize: 10,
        color: '#cdd6f4',
      },
    }],
  };
}

chart.setOption(getOption(
  buildDisplayNodes(litNodes, displayDepth),
  buildDisplayEdges(litNodes, displayDepth)
));

document.getElementById('stats').textContent =
  '节点: ' + stats.total_nodes + ' | 边: ' + stats.total_edges;
if (stats.truncated) document.getElementById('warn').style.display = 'block';

function getSpeedDelay() {
  return SPEED_DELAYS[parseInt(document.getElementById('sl-speed').value) - 1];
}

function animateNextLayer() {
  currentAnimDepth++;
  var key = String(currentAnimDepth);
  var layer = depthLevels[key];
  if (!layer || currentAnimDepth > displayDepth) {
    stopAnimation();
    document.getElementById('btn-play').innerHTML = '&#9654; 播放';
    return;
  }
  layer.forEach(function(id) { litNodes.add(id); });
  chart.setOption({
    series: [{
      data: buildDisplayNodes(litNodes, displayDepth),
      links: buildDisplayEdges(litNodes, displayDepth),
    }],
  });
  animTimer = setTimeout(animateNextLayer, getSpeedDelay());
}

function stopAnimation() {
  if (animTimer) { clearTimeout(animTimer); animTimer = null; }
  isPlaying = false;
}

document.getElementById('btn-play').addEventListener('click', function() {
  if (isPlaying) {
    stopAnimation();
    this.innerHTML = '&#9654; 播放';
  } else {
    isPlaying = true;
    this.innerHTML = '&#9646;&#9646; 暂停';
    animateNextLayer();
  }
});

document.getElementById('btn-reset').addEventListener('click', function() {
  stopAnimation();
  document.getElementById('btn-play').innerHTML = '&#9654; 播放';
  currentAnimDepth = 0;
  litNodes = new Set([rootId]);
  chart.setOption({
    series: [{
      data: buildDisplayNodes(litNodes, displayDepth),
      links: buildDisplayEdges(litNodes, displayDepth),
    }],
  });
});

var slDepth = document.getElementById('sl-depth');
var lblDepth = document.getElementById('lbl-depth');
slDepth.max = maxDataDepth || 20;
slDepth.value = maxDataDepth || 20;
displayDepth = parseInt(slDepth.value);
lblDepth.textContent = displayDepth;

slDepth.addEventListener('input', function() {
  displayDepth = parseInt(this.value);
  lblDepth.textContent = displayDepth;
  chart.setOption({
    series: [{
      data: buildDisplayNodes(litNodes, displayDepth),
      links: buildDisplayEdges(litNodes, displayDepth),
    }],
  });
});

document.getElementById('sl-speed').addEventListener('input', function() {
  document.getElementById('lbl-speed').textContent = this.value + 'x';
});

document.getElementById('btn-fs').addEventListener('click', function() {
  var wrap = document.getElementById('wrap');
  if (!document.fullscreenElement) {
    wrap.requestFullscreen && wrap.requestFullscreen();
  } else {
    document.exitFullscreen && document.exitFullscreen();
  }
});
document.addEventListener('fullscreenchange', function() {
  setTimeout(function() { chart.resize(); }, 100);
});

window.addEventListener('resize', function() { chart.resize(); });
</script>
</body>
</html>"""

    return part1 + graph_json + part2
