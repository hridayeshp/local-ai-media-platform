let pollTimer = null;
let activeJobId = null;

const editorState = {
  assets: [],
  video_tracks: [{ id: "v1", clips: [] }],
  audio_tracks: [{ id: "a1", clips: [] }],
  text_tracks: [{ id: "t1", clips: [] }],
  counters: {
    clip: 1,
    videoTrack: 1,
    audioTrack: 1,
    textClip: 1
  },
  zoomPxPerSec: 90,
  snapEnabled: true,
  snapSeconds: 0.25,
  selected: null
};

const waveformCache = {};
let renderQueued = false;
let dragState = null;
let audioContext = null;

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function setProgress(value) {
  const safe = Math.max(0, Math.min(100, value || 0));
  document.getElementById("progressFill").style.width = `${safe}%`;
  document.getElementById("progressText").textContent = `${safe}%`;
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function resetOutput() {
  const video = document.getElementById("resultVideo");
  const link = document.getElementById("downloadLink");
  video.pause();
  video.removeAttribute("src");
  video.style.display = "none";
  link.removeAttribute("href");
  link.style.display = "none";
}

function jobLine(job) {
  return `Job ${job.job_id} • ${job.status} • stage=${job.stage} • providers=${job.video_provider || "-"} / ${job.audio_provider || "-"}`;
}

async function fetchJob(jobId) {
  const res = await fetch(`/api/jobs/${jobId}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to fetch job");
  }
  return res.json();
}

async function pollJob(jobId) {
  try {
    const job = await fetchJob(jobId);
    document.getElementById("status").textContent = job.status === "completed" ? "Completed" : "Processing";
    document.getElementById("jobMeta").textContent = jobLine(job);
    setProgress(job.progress);

    if (job.status === "completed") {
      stopPolling();
      const downloadUrl = `/api/jobs/${jobId}/download`;
      const video = document.getElementById("resultVideo");
      const link = document.getElementById("downloadLink");
      video.src = `${downloadUrl}?t=${Date.now()}`;
      video.style.display = "block";
      link.href = downloadUrl;
      link.style.display = "inline-block";
      document.getElementById("generateBtn").disabled = false;
    }

    if (job.status === "failed") {
      stopPolling();
      document.getElementById("status").textContent = `Failed: ${job.error || "Job failed"}`;
      document.getElementById("generateBtn").disabled = false;
    }
  } catch (e) {
    stopPolling();
    document.getElementById("status").textContent = `Error: ${e.message || "Polling failed"}`;
    document.getElementById("generateBtn").disabled = false;
  }
}

async function startJob() {
  const prompt = document.getElementById("prompt").value.trim();
  const narration = document.getElementById("narration").value.trim();
  const useReplicate = document.getElementById("useReplicate").checked;
  const useElevenlabs = document.getElementById("useElevenlabs").checked;
  const status = document.getElementById("status");
  const button = document.getElementById("generateBtn");

  if (!prompt) {
    status.textContent = "Prompt is required";
    return;
  }

  stopPolling();
  resetOutput();
  setProgress(0);
  button.disabled = true;
  status.textContent = "Queueing job...";
  document.getElementById("jobMeta").textContent = "";

  try {
    const res = await fetch("/api/jobs/video", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt,
        narration: narration || null,
        use_replicate: useReplicate,
        use_elevenlabs: useElevenlabs
      })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Failed to create job");
    }
    const data = await res.json();
    activeJobId = data.job_id;
    status.textContent = "Job queued";
    document.getElementById("jobMeta").textContent = `Job ${activeJobId} queued`;
    pollTimer = setInterval(() => pollJob(activeJobId), 2000);
    pollJob(activeJobId);
  } catch (e) {
    button.disabled = false;
    status.textContent = `Error: ${e.message || "Request failed"}`;
  }
}

function setEditorStatus(message) {
  document.getElementById("editorStatus").textContent = message;
}

function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "-";
  return `${seconds.toFixed(2)}s`;
}

function getTrackList(type) {
  return editorState[`${type}_tracks`];
}

function getAssetById(assetId) {
  return editorState.assets.find((a) => a.asset_id === assetId) || null;
}

function getAssetDuration(assetId) {
  const asset = getAssetById(assetId);
  return asset ? Number(asset.duration || 0) : 0;
}

function getTrackEnd(track, type) {
  return track.clips.reduce((max, clip) => {
    const end = type === "text"
      ? Number(clip.end || Number(clip.start || 0) + 2)
      : Number(clip.start || 0) + Number(clip.duration || 0);
    return Math.max(max, end);
  }, 0);
}

function nextClipId(prefix = "clip") {
  editorState.counters.clip += 1;
  return `${prefix}_${editorState.counters.clip}`;
}

function quantizeValue(value, bypassSnap = false) {
  const raw = Number(value);
  if (!Number.isFinite(raw)) return 0;
  if (!editorState.snapEnabled || bypassSnap) return raw;
  const step = editorState.snapSeconds || 0.25;
  return Math.round(raw / step) * step;
}

function clipDuration(type, clip) {
  if (type === "text") {
    return Math.max(0.05, Number(clip.end || 0) - Number(clip.start || 0));
  }
  return Math.max(0.1, Number(clip.duration || 0.1));
}

function normalizeClip(type, clip) {
  clip.start = Math.max(0, Number(clip.start || 0));

  if (type === "text") {
    const end = Number(clip.end || clip.start + 2);
    clip.end = Math.max(clip.start + 0.05, end);
    clip.font_size = Math.max(10, Number(clip.font_size || 42));
    clip.x = Number(clip.x || 40);
    clip.y = Number(clip.y || 620);
    clip.text = String(clip.text || "");
    return;
  }

  clip.in_point = Math.max(0, Number(clip.in_point || 0));
  clip.duration = Math.max(0.1, Number(clip.duration || 0.1));
  clip.transition_in = Math.max(0, Number(clip.transition_in || 0));
  clip.transition_out = Math.max(0, Number(clip.transition_out || 0));
  clip.volume = Math.max(0, Number(clip.volume || 1));

  const maxTransition = clip.duration / 2;
  clip.transition_in = Math.min(clip.transition_in, maxTransition);
  clip.transition_out = Math.min(clip.transition_out, maxTransition);

  const assetDuration = getAssetDuration(clip.asset_id);
  if (assetDuration > 0 && clip.in_point + clip.duration > assetDuration) {
    clip.duration = Math.max(0.1, assetDuration - clip.in_point);
  }
}

function getTrack(type, index) {
  return getTrackList(type)[index];
}

function findClip(type, trackIndex, clipId) {
  const track = getTrack(type, trackIndex);
  if (!track) return null;
  return track.clips.find((clip) => clip.id === clipId) || null;
}

function selectClip(type, trackIndex, clipId) {
  editorState.selected = { type, trackIndex, clipId };
  renderInspector();
  scheduleTimelineRender();
}

function isSelected(type, trackIndex, clipId) {
  const sel = editorState.selected;
  return !!sel && sel.type === type && sel.trackIndex === trackIndex && sel.clipId === clipId;
}

function setZoom(value) {
  editorState.zoomPxPerSec = Math.max(40, Math.min(220, Number(value || 90)));
  document.getElementById("zoomValue").textContent = String(Math.round(editorState.zoomPxPerSec));
  scheduleTimelineRender();
}

function toggleSnap(enabled) {
  editorState.snapEnabled = !!enabled;
}

function setSnapSeconds(value) {
  const sec = Number(value);
  if (Number.isFinite(sec) && sec > 0) editorState.snapSeconds = sec;
}

function addTrack(type) {
  const tracks = getTrackList(type);
  if (type === "video") {
    editorState.counters.videoTrack += 1;
    tracks.push({ id: `v${editorState.counters.videoTrack}`, clips: [] });
  } else if (type === "audio") {
    editorState.counters.audioTrack += 1;
    tracks.push({ id: `a${editorState.counters.audioTrack}`, clips: [] });
  }
  renderAssets();
  scheduleTimelineRender();
}

async function refreshAssets() {
  try {
    const res = await fetch("/api/editor/assets");
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Failed to load assets");
    }
    const data = await res.json();
    editorState.assets = data.assets || [];
    renderAssets();
    setEditorStatus(`Loaded ${editorState.assets.length} assets`);
  } catch (e) {
    setEditorStatus(`Error: ${e.message || "Failed to load assets"}`);
  }
}

async function uploadAssets() {
  const input = document.getElementById("assetUpload");
  const files = Array.from(input.files || []);
  if (files.length === 0) {
    setEditorStatus("Select one or more files first");
    return;
  }

  let success = 0;
  for (let i = 0; i < files.length; i += 1) {
    const file = files[i];
    const form = new FormData();
    form.append("file", file);
    setEditorStatus(`Uploading ${file.name} (${i + 1}/${files.length})`);

    const res = await fetch("/api/editor/assets/upload", {
      method: "POST",
      body: form
    });
    if (res.ok) {
      success += 1;
    } else {
      const err = await res.json().catch(() => ({}));
      setEditorStatus(`Upload failed for ${file.name}: ${err.detail || "Unknown error"}`);
    }
  }

  input.value = "";
  await refreshAssets();
  setEditorStatus(`Uploaded ${success}/${files.length} files`);
}

function renderAssets() {
  const root = document.getElementById("assetGrid");
  if (!editorState.assets.length) {
    root.innerHTML = "<div class='asset-card'>No assets yet. Upload video/audio files to start editing.</div>";
    return;
  }

  root.innerHTML = editorState.assets.map((asset) => {
    const videoButtons = getTrackList("video").map((track, index) => (
      `<button onclick="addClipToTrack('video', ${index}, '${asset.asset_id}')">+${escapeHtml(track.id)}</button>`
    )).join("");
    const audioButtons = getTrackList("audio").map((track, index) => (
      `<button onclick="addClipToTrack('audio', ${index}, '${asset.asset_id}')">+${escapeHtml(track.id)}</button>`
    )).join("");

    return `
      <div class="asset-card">
        <div class="asset-name">${escapeHtml(asset.original_name)}</div>
        <div class="asset-meta">
          kind=${escapeHtml(asset.kind)} | duration=${formatDuration(Number(asset.duration || 0))} | size=${Math.round((asset.size_bytes || 0) / 1024)} KB
        </div>
        <div class="asset-actions">
          ${videoButtons}
          ${audioButtons}
        </div>
      </div>
    `;
  }).join("");
}

function addClipToTrack(type, trackIndex, assetId) {
  const track = getTrackList(type)[trackIndex];
  const asset = getAssetById(assetId);
  if (!track || !asset) return;

  if (type === "video" && !asset.has_video) {
    setEditorStatus(`Asset ${asset.original_name} has no video stream`);
    return;
  }
  if (type === "audio" && !asset.has_audio) {
    setEditorStatus(`Asset ${asset.original_name} has no audio stream`);
    return;
  }

  const defaultDuration = Math.max(0.5, Math.min(Number(asset.duration || 4), 8));
  const clip = {
    id: nextClipId(type === "video" ? "vclip" : "aclip"),
    asset_id: asset.asset_id,
    start: Number(getTrackEnd(track, type).toFixed(2)),
    in_point: 0,
    duration: Number(defaultDuration.toFixed(2)),
    transition_in: 0,
    transition_out: 0,
    volume: 1
  };
  normalizeClip(type, clip);
  track.clips.push(clip);
  setEditorStatus(`Added ${asset.original_name} to ${track.id}`);
  if (type === "audio" || (type === "video" && asset.has_audio)) {
    ensureWaveform(asset.asset_id);
  }
  scheduleTimelineRender();
}

function updateClipField(type, trackIndex, clipId, field, value) {
  const clip = findClip(type, trackIndex, clipId);
  if (!clip) return;

  const numberFields = new Set([
    "start", "in_point", "duration", "transition_in", "transition_out", "volume", "end", "font_size", "x", "y"
  ]);

  if (numberFields.has(field)) {
    const n = Number(value);
    clip[field] = Number.isFinite(n) ? n : 0;
  } else {
    clip[field] = value;
  }

  normalizeClip(type, clip);
  scheduleTimelineRender();
}

function removeClip(type, trackIndex, clipId) {
  const track = getTrack(type, trackIndex);
  if (!track) return;
  track.clips = track.clips.filter((clip) => clip.id !== clipId);
  if (isSelected(type, trackIndex, clipId)) {
    editorState.selected = null;
  }
  scheduleTimelineRender();
}

function splitClip(type, trackIndex, clipId) {
  const track = getTrack(type, trackIndex);
  const clip = findClip(type, trackIndex, clipId);
  if (!track || !clip || type === "text") return;

  const answer = prompt("Split at offset seconds from clip start:", (clip.duration / 2).toFixed(2));
  if (answer === null) return;
  const splitAt = Number(answer);
  if (!Number.isFinite(splitAt) || splitAt <= 0.05 || splitAt >= clip.duration - 0.05) {
    setEditorStatus("Invalid split point");
    return;
  }

  const originalDuration = clip.duration;
  const originalStart = clip.start;
  const originalIn = clip.in_point;
  const originalOutFade = clip.transition_out || 0;

  clip.duration = Number(splitAt.toFixed(2));
  clip.transition_out = 0;
  normalizeClip(type, clip);

  const second = {
    ...clip,
    id: nextClipId(type === "video" ? "vclip" : "aclip"),
    start: Number((originalStart + splitAt).toFixed(2)),
    in_point: Number((originalIn + splitAt).toFixed(2)),
    duration: Number((originalDuration - splitAt).toFixed(2)),
    transition_in: 0,
    transition_out: originalOutFade
  };
  normalizeClip(type, second);
  track.clips.push(second);
  track.clips.sort((a, b) => a.start - b.start);
  scheduleTimelineRender();
}

function addCaption() {
  const text = document.getElementById("captionText").value.trim();
  const start = Number(document.getElementById("captionStart").value || 0);
  const end = Number(document.getElementById("captionEnd").value || 0);
  const color = document.getElementById("captionColor").value.trim() || "white";
  const size = Number(document.getElementById("captionSize").value || 42);
  if (!text) {
    setEditorStatus("Caption text is required");
    return;
  }
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
    setEditorStatus("Caption start/end is invalid");
    return;
  }
  const track = editorState.text_tracks[0];
  const clip = {
    id: `tclip_${++editorState.counters.textClip}`,
    text,
    start,
    end,
    font_size: Math.max(10, Math.min(120, size)),
    color,
    x: 40,
    y: 620
  };
  normalizeClip("text", clip);
  track.clips.push(clip);
  document.getElementById("captionText").value = "";
  scheduleTimelineRender();
}

function buildTimelineLanes() {
  const lanes = [];
  editorState.video_tracks.forEach((track, index) => {
    lanes.push({ type: "video", index, id: track.id, clips: track.clips });
  });
  editorState.audio_tracks.forEach((track, index) => {
    lanes.push({ type: "audio", index, id: track.id, clips: track.clips });
  });
  editorState.text_tracks.forEach((track, index) => {
    lanes.push({ type: "text", index, id: track.id, clips: track.clips });
  });
  return lanes;
}

function projectDurationSeconds() {
  let maxEnd = 6;
  buildTimelineLanes().forEach((lane) => {
    lane.clips.forEach((clip) => {
      const end = lane.type === "text"
        ? Number(clip.end || Number(clip.start || 0) + 2)
        : Number(clip.start || 0) + Number(clip.duration || 0);
      maxEnd = Math.max(maxEnd, end + 0.5);
    });
  });
  return Math.max(6, maxEnd);
}

function clipLabel(laneType, clip) {
  if (laneType === "text") return clip.text || "Caption";
  const asset = getAssetById(clip.asset_id);
  return asset ? asset.original_name : "Asset";
}

function renderTimeline() {
  const root = document.getElementById("timelineRoot");
  const lanes = buildTimelineLanes();
  const duration = projectDurationSeconds();
  const pxPerSec = editorState.zoomPxPerSec;
  const laneWidth = Math.max(520, Math.ceil(duration * pxPerSec) + 40);

  let rulerTicks = "";
  for (let t = 0; t <= duration + 0.001; t += 1) {
    const left = Math.round(t * pxPerSec);
    rulerTicks += `<div class="tick" style="left:${left + 72}px"><span>${t}s</span></div>`;
  }

  const minorStep = editorState.snapSeconds || 0.25;
  const laneHtml = lanes.map((lane) => {
    let grid = "";
    for (let t = 0; t <= duration + 0.001; t += minorStep) {
      const left = Math.round(t * pxPerSec);
      grid += `<div class="lane-grid-line" style="left:${left}px"></div>`;
    }

    const clips = lane.clips.map((clip) => {
      normalizeClip(lane.type, clip);
      const start = Number(clip.start || 0);
      const dur = clipDuration(lane.type, clip);
      const left = Math.round(start * pxPerSec);
      const width = Math.max(34, Math.round(dur * pxPerSec));
      const selectedClass = isSelected(lane.type, lane.index, clip.id) ? "selected" : "";
      const wave = lane.type === "audio" && clip.asset_id
        ? `<canvas class="wave-canvas" data-wave-asset="${clip.asset_id}" data-wave-id="${clip.id}" width="160" height="20"></canvas>`
        : "";
      const sub = lane.type === "text"
        ? `${dur.toFixed(2)}s`
        : `in ${Number(clip.in_point || 0).toFixed(2)} • dur ${Number(clip.duration || 0).toFixed(2)}`;

      return `
        <div class="clip-block ${lane.type} ${selectedClass}" data-type="${lane.type}" data-track-index="${lane.index}" data-clip-id="${clip.id}" style="left:${left}px;width:${width}px">
          <div class="clip-handle left" data-handle="left"></div>
          <div class="clip-inner" data-action="move">
            <div class="clip-title">${escapeHtml(clipLabel(lane.type, clip))}</div>
            <div class="clip-sub">${escapeHtml(sub)}</div>
            ${wave}
          </div>
          <div class="clip-handle right" data-handle="right"></div>
        </div>
      `;
    }).join("");

    return `
      <div class="track-lane">
        <div class="lane-header">${escapeHtml(lane.type[0].toUpperCase() + lane.id)}</div>
        <div class="lane-surface" style="width:${laneWidth}px">
          <div class="lane-grid">${grid}</div>
          ${clips}
        </div>
      </div>
    `;
  }).join("");

  root.innerHTML = `
    <div class="time-ruler" style="width:${laneWidth + 72}px">${rulerTicks}</div>
    ${laneHtml}
  `;

  bindTimelineInteractions();
  drawWaveforms();
  renderInspector();
}

function scheduleTimelineRender() {
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(() => {
    renderQueued = false;
    renderTimeline();
  });
}

function bindTimelineInteractions() {
  const blocks = Array.from(document.querySelectorAll(".clip-block"));
  blocks.forEach((block) => {
    const laneType = block.dataset.type;
    const trackIndex = Number(block.dataset.trackIndex);
    const clipId = block.dataset.clipId;

    block.addEventListener("mousedown", (event) => {
      if (event.button !== 0) return;
      selectClip(laneType, trackIndex, clipId);
    });

    const inner = block.querySelector(".clip-inner");
    const left = block.querySelector(".clip-handle.left");
    const right = block.querySelector(".clip-handle.right");

    if (inner) {
      inner.addEventListener("mousedown", (event) => {
        if (event.button !== 0) return;
        startDrag(event, laneType, trackIndex, clipId, "move");
      });
    }
    if (left) {
      left.addEventListener("mousedown", (event) => {
        if (event.button !== 0) return;
        startDrag(event, laneType, trackIndex, clipId, "left");
      });
    }
    if (right) {
      right.addEventListener("mousedown", (event) => {
        if (event.button !== 0) return;
        startDrag(event, laneType, trackIndex, clipId, "right");
      });
    }
  });
}

function startDrag(event, type, trackIndex, clipId, mode) {
  event.preventDefault();
  const clip = findClip(type, trackIndex, clipId);
  if (!clip) return;
  selectClip(type, trackIndex, clipId);
  dragState = {
    type,
    trackIndex,
    clipId,
    mode,
    startX: event.clientX,
    originalStart: Number(clip.start || 0),
    originalDuration: clipDuration(type, clip),
    originalInPoint: Number(clip.in_point || 0),
    originalEnd: Number(clip.end || 0)
  };
}

function handleDragMove(event) {
  if (!dragState) return;
  const clip = findClip(dragState.type, dragState.trackIndex, dragState.clipId);
  if (!clip) return;

  const deltaPx = event.clientX - dragState.startX;
  const deltaSecRaw = deltaPx / editorState.zoomPxPerSec;
  const bypassSnap = event.shiftKey;

  if (dragState.mode === "move") {
    let nextStart = quantizeValue(dragState.originalStart + deltaSecRaw, bypassSnap);
    nextStart = Math.max(0, nextStart);
    clip.start = nextStart;
    if (dragState.type === "text") {
      const dur = dragState.originalEnd - dragState.originalStart;
      clip.end = clip.start + dur;
    }
  } else if (dragState.mode === "left") {
    if (dragState.type === "text") {
      let nextStart = quantizeValue(dragState.originalStart + deltaSecRaw, bypassSnap);
      nextStart = Math.max(0, Math.min(nextStart, dragState.originalEnd - 0.05));
      clip.start = nextStart;
    } else {
      const fixedEnd = dragState.originalStart + dragState.originalDuration;
      let nextStart = quantizeValue(dragState.originalStart + deltaSecRaw, bypassSnap);
      nextStart = Math.max(0, Math.min(nextStart, fixedEnd - 0.1));
      clip.start = nextStart;
      clip.duration = fixedEnd - nextStart;
      clip.in_point = Math.max(0, dragState.originalInPoint + (nextStart - dragState.originalStart));
    }
  } else if (dragState.mode === "right") {
    if (dragState.type === "text") {
      let nextEnd = quantizeValue(dragState.originalEnd + deltaSecRaw, bypassSnap);
      nextEnd = Math.max(Number(clip.start || 0) + 0.05, nextEnd);
      clip.end = nextEnd;
    } else {
      let nextDuration = quantizeValue(dragState.originalDuration + deltaSecRaw, bypassSnap);
      nextDuration = Math.max(0.1, nextDuration);
      clip.duration = nextDuration;
    }
  }

  normalizeClip(dragState.type, clip);
  scheduleTimelineRender();
}

function endDrag() {
  dragState = null;
}

function renderInspector() {
  const root = document.getElementById("clipInspector");
  const sel = editorState.selected;
  if (!sel) {
    root.innerHTML = "Select a clip in the timeline";
    return;
  }
  const clip = findClip(sel.type, sel.trackIndex, sel.clipId);
  if (!clip) {
    root.innerHTML = "Select a clip in the timeline";
    return;
  }

  const base = `
    <div class="inspector-grid">
      <label>start<input type="number" step="0.1" min="0" value="${Number(clip.start || 0).toFixed(2)}" onchange="updateClipField('${sel.type}', ${sel.trackIndex}, '${sel.clipId}', 'start', this.value)" /></label>
      ${sel.type !== "text" ? `<label>in<input type="number" step="0.1" min="0" value="${Number(clip.in_point || 0).toFixed(2)}" onchange="updateClipField('${sel.type}', ${sel.trackIndex}, '${sel.clipId}', 'in_point', this.value)" /></label>` : ""}
      ${sel.type !== "text" ? `<label>duration<input type="number" step="0.1" min="0.1" value="${Number(clip.duration || 0.1).toFixed(2)}" onchange="updateClipField('${sel.type}', ${sel.trackIndex}, '${sel.clipId}', 'duration', this.value)" /></label>` : ""}
      ${sel.type === "text" ? `<label>end<input type="number" step="0.1" min="0.1" value="${Number(clip.end || Number(clip.start || 0) + 2).toFixed(2)}" onchange="updateClipField('${sel.type}', ${sel.trackIndex}, '${sel.clipId}', 'end', this.value)" /></label>` : ""}
      ${sel.type !== "text" ? `<label>volume<input type="number" step="0.1" min="0" value="${Number(clip.volume || 1).toFixed(2)}" onchange="updateClipField('${sel.type}', ${sel.trackIndex}, '${sel.clipId}', 'volume', this.value)" /></label>` : ""}
      ${sel.type === "video" ? `<label>fade in<input type="number" step="0.1" min="0" value="${Number(clip.transition_in || 0).toFixed(2)}" onchange="updateClipField('${sel.type}', ${sel.trackIndex}, '${sel.clipId}', 'transition_in', this.value)" /></label>` : ""}
      ${sel.type === "video" ? `<label>fade out<input type="number" step="0.1" min="0" value="${Number(clip.transition_out || 0).toFixed(2)}" onchange="updateClipField('${sel.type}', ${sel.trackIndex}, '${sel.clipId}', 'transition_out', this.value)" /></label>` : ""}
      ${sel.type === "text" ? `<label>text<input type="text" value="${escapeHtml(clip.text || "")}" onchange="updateClipField('${sel.type}', ${sel.trackIndex}, '${sel.clipId}', 'text', this.value)" /></label>` : ""}
      ${sel.type === "text" ? `<label>size<input type="number" step="1" min="10" value="${Number(clip.font_size || 42)}" onchange="updateClipField('${sel.type}', ${sel.trackIndex}, '${sel.clipId}', 'font_size', this.value)" /></label>` : ""}
      ${sel.type === "text" ? `<label>color<input type="text" value="${escapeHtml(clip.color || "white")}" onchange="updateClipField('${sel.type}', ${sel.trackIndex}, '${sel.clipId}', 'color', this.value)" /></label>` : ""}
    </div>
    <div class="clip-actions" style="margin-top:0.6rem;">
      ${sel.type !== "text" ? `<button onclick="splitClip('${sel.type}', ${sel.trackIndex}, '${sel.clipId}')">Split</button>` : ""}
      <button onclick="removeClip('${sel.type}', ${sel.trackIndex}, '${sel.clipId}')">Remove</button>
    </div>
  `;
  root.innerHTML = base;
}

function getAudioContext() {
  if (!audioContext) {
    const ACtx = window.AudioContext || window.webkitAudioContext;
    if (ACtx) audioContext = new ACtx();
  }
  return audioContext;
}

function downsampleWaveform(buffer, bins = 120) {
  const channel = buffer.getChannelData(0);
  const blockSize = Math.max(1, Math.floor(channel.length / bins));
  const out = [];
  for (let i = 0; i < bins; i += 1) {
    const start = i * blockSize;
    const end = Math.min(channel.length, start + blockSize);
    let sum = 0;
    let count = 0;
    for (let j = start; j < end; j += 1) {
      sum += Math.abs(channel[j]);
      count += 1;
    }
    out.push(count ? sum / count : 0);
  }
  const max = Math.max(...out, 0.001);
  return out.map((v) => v / max);
}

async function ensureWaveform(assetId) {
  if (!assetId) return null;
  if (waveformCache[assetId]?.bins) return waveformCache[assetId].bins;
  if (waveformCache[assetId]?.promise) return waveformCache[assetId].promise;

  const promise = (async () => {
    try {
      const ctx = getAudioContext();
      if (!ctx) return null;
      const res = await fetch(`/api/editor/assets/${assetId}/download`);
      if (!res.ok) return null;
      const arr = await res.arrayBuffer();
      const buffer = await ctx.decodeAudioData(arr.slice(0));
      const bins = downsampleWaveform(buffer, 140);
      waveformCache[assetId] = { bins };
      return bins;
    } catch {
      waveformCache[assetId] = { bins: null };
      return null;
    }
  })();

  waveformCache[assetId] = { promise };
  const bins = await promise;
  waveformCache[assetId] = { bins };
  scheduleTimelineRender();
  return bins;
}

function drawWaveOnCanvas(canvas, bins) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const w = canvas.clientWidth || 120;
  const h = canvas.clientHeight || 20;
  if (canvas.width !== w) canvas.width = w;
  if (canvas.height !== h) canvas.height = h;

  ctx.clearRect(0, 0, w, h);
  if (!bins || bins.length === 0) return;
  const barCount = Math.max(16, Math.floor(w / 3));
  const step = bins.length / barCount;
  ctx.fillStyle = "rgba(255,255,255,0.72)";
  for (let i = 0; i < barCount; i += 1) {
    const idx = Math.floor(i * step);
    const amp = bins[Math.min(idx, bins.length - 1)] || 0;
    const barH = Math.max(1, amp * h);
    const x = i * (w / barCount);
    ctx.fillRect(x, (h - barH) / 2, Math.max(1, w / barCount - 1), barH);
  }
}

function drawWaveforms() {
  const canvases = Array.from(document.querySelectorAll("canvas[data-wave-asset]"));
  canvases.forEach((canvas) => {
    const assetId = canvas.dataset.waveAsset;
    const cached = waveformCache[assetId]?.bins;
    if (cached) {
      drawWaveOnCanvas(canvas, cached);
    } else {
      ensureWaveform(assetId).then((bins) => {
        if (bins) drawWaveOnCanvas(canvas, bins);
      });
    }
  });
}

function sanitizeTrack(type, track) {
  const clips = track.clips.map((clip) => {
    if (type === "text") {
      return {
        id: clip.id,
        text: String(clip.text || ""),
        start: Number(clip.start || 0),
        end: Number(clip.end || Number(clip.start || 0) + 2),
        font_size: Number(clip.font_size || 42),
        color: String(clip.color || "white"),
        x: Number(clip.x || 40),
        y: Number(clip.y || 620)
      };
    }
    return {
      id: clip.id,
      asset_id: clip.asset_id,
      start: Number(clip.start || 0),
      in_point: Number(clip.in_point || 0),
      duration: Math.max(0.1, Number(clip.duration || 0.1)),
      transition_in: Number(clip.transition_in || 0),
      transition_out: Number(clip.transition_out || 0),
      volume: Math.max(0, Number(clip.volume || 1))
    };
  });
  return { id: track.id, clips };
}

function buildExportPayload() {
  const width = Number(document.getElementById("exportWidth").value || 1280);
  const height = Number(document.getElementById("exportHeight").value || 720);
  const fps = Number(document.getElementById("exportFps").value || 24);
  return {
    width: Math.max(320, Math.floor(width)),
    height: Math.max(240, Math.floor(height)),
    fps: Math.max(12, Math.min(60, Math.floor(fps))),
    bg_color: "black",
    video_tracks: editorState.video_tracks.map((track) => sanitizeTrack("video", track)),
    audio_tracks: editorState.audio_tracks.map((track) => sanitizeTrack("audio", track)),
    text_tracks: editorState.text_tracks.map((track) => sanitizeTrack("text", track))
  };
}

async function exportProject() {
  const status = document.getElementById("exportStatus");
  const button = document.getElementById("exportBtn");
  const payload = buildExportPayload();

  const hasVideo = payload.video_tracks.some((track) => track.clips.length > 0);
  const hasText = payload.text_tracks.some((track) => track.clips.length > 0);
  if (!hasVideo && !hasText) {
    status.textContent = "Add at least one video or caption clip before export";
    return;
  }

  button.disabled = true;
  status.textContent = "Exporting timeline...";
  try {
    const res = await fetch("/api/editor/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Export failed");
    }
    const data = await res.json();
    const video = document.getElementById("exportVideo");
    const link = document.getElementById("exportDownloadLink");
    const downloadUrl = `/api/editor/exports/${data.export_id}/download`;
    video.src = `${downloadUrl}?t=${Date.now()}`;
    video.style.display = "block";
    link.href = downloadUrl;
    link.style.display = "inline-block";
    status.textContent = `Export complete (${Number(data.duration || 0).toFixed(2)}s)`;
  } catch (e) {
    status.textContent = `Error: ${e.message || "Export failed"}`;
  } finally {
    button.disabled = false;
  }
}

window.addEventListener("mousemove", handleDragMove);
window.addEventListener("mouseup", endDrag);

window.addEventListener("load", async () => {
  setProgress(0);
  setZoom(document.getElementById("zoomSlider").value);
  toggleSnap(document.getElementById("snapToggle").checked);
  setSnapSeconds(document.getElementById("snapSelect").value);
  renderTimeline();
  await refreshAssets();
});
