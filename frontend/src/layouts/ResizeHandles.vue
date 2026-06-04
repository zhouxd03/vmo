<script setup>
// Thin transparent grips along the window border. On a frameless pywebview
// window there are no native resize edges, so on mousedown we hand off to the
// OS resize loop via api.start_resize(edge). The Vue layout itself is fully
// responsive (flex / 100vh / overflow:auto), so stretching never distorts UI.
const EDGES = [
  'top', 'bottom', 'left', 'right',
  'topleft', 'topright', 'bottomleft', 'bottomright',
]

function onDown(edge, e) {
  if (e.button !== 0) return
  const api = window.pywebview && window.pywebview.api
  if (api && api.start_resize) {
    e.preventDefault()
    api.start_resize(edge)
  }
}
</script>

<template>
  <div class="resize-layer">
    <div
      v-for="edge in EDGES"
      :key="edge"
      :class="['rz', 'rz-' + edge]"
      @mousedown="onDown(edge, $event)"
    />
  </div>
</template>

<style scoped>
/* Handles sit above everything but only occupy a few px at the border so they
   never block normal interaction with the app content. */
.resize-layer {
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 9999;
}
.rz {
  position: absolute;
  pointer-events: auto;
}
.rz-top    { top: 0; left: 6px; right: 6px; height: 5px; cursor: ns-resize; }
.rz-bottom { bottom: 0; left: 6px; right: 6px; height: 5px; cursor: ns-resize; }
.rz-left   { left: 0; top: 6px; bottom: 6px; width: 5px; cursor: ew-resize; }
.rz-right  { right: 0; top: 6px; bottom: 6px; width: 5px; cursor: ew-resize; }
.rz-topleft     { top: 0; left: 0; width: 8px; height: 8px; cursor: nwse-resize; }
.rz-topright    { top: 0; right: 0; width: 8px; height: 8px; cursor: nesw-resize; }
.rz-bottomleft  { bottom: 0; left: 0; width: 8px; height: 8px; cursor: nesw-resize; }
.rz-bottomright { bottom: 0; right: 0; width: 8px; height: 8px; cursor: nwse-resize; }
</style>
