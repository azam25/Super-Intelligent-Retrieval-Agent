const uploadForm = document.getElementById("uploadForm");
const fileInput = document.getElementById("fileInput");
const uploadBtn = document.getElementById("uploadBtn");
const docName = document.getElementById("docName");
const chunkCount = document.getElementById("chunkCount");
const indexTime = document.getElementById("indexTime");
const timeline = document.getElementById("timeline");
const appStatus = document.getElementById("appStatus");
const progressWrap = document.getElementById("progressWrap");
const progressLabel = document.getElementById("progressLabel");
const progressValue = document.getElementById("progressValue");
const progressFill = document.getElementById("progressFill");
const progressHint = document.getElementById("progressHint");

const chatForm = document.getElementById("chatForm");
const messageInput = document.getElementById("messageInput");
const chatLog = document.getElementById("chatLog");
const retrievalList = document.getElementById("retrievalList");
const sendBtn = document.getElementById("sendBtn");

let hasIndex = false;
let progressTimer = null;

function setAppStatus(state, text) {
  appStatus.classList.remove("idle", "processing", "done", "error");
  appStatus.classList.add(state);
  appStatus.textContent = text;
}

function setProgressState(state) {
  progressWrap.classList.remove("processing", "done", "error");
  if (state === "processing" || state === "done" || state === "error") {
    progressWrap.classList.add(state);
  }
}

function setProgress(percent, label, hint) {
  const clamped = Math.max(0, Math.min(100, percent));
  progressFill.style.width = `${clamped}%`;
  progressValue.textContent = `${Math.round(clamped)}%`;
  progressLabel.textContent = label;
  progressHint.textContent = hint;
}

function clearProgressTimer() {
  if (progressTimer) {
    clearInterval(progressTimer);
    progressTimer = null;
  }
}

function startProgressSimulation({ label, hint, start = 10, max = 88, step = 2, intervalMs = 300 }) {
  clearProgressTimer();
  setProgressState("processing");
  setProgress(start, label, hint);
  setAppStatus("processing", "Processing");

  let current = start;
  progressTimer = setInterval(() => {
    current = Math.min(max, current + step);
    setProgress(current, label, hint);
    if (current >= max) {
      clearProgressTimer();
    }
  }, intervalMs);
}

function completeProgress(successLabel, successHint) {
  clearProgressTimer();
  setProgressState("done");
  setProgress(100, successLabel, successHint);
  setAppStatus("done", "Ready");
}

function failProgress(message) {
  clearProgressTimer();
  setProgressState("error");
  setProgress(100, "Failed", message);
  setAppStatus("error", "Error");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function replayStageProgress(stages) {
  if (!stages || !stages.length) {
    completeProgress("Completed", "Request finished.");
    return;
  }

  clearProgressTimer();
  setProgressState("processing");
  setAppStatus("processing", "Processing");

  const step = 90 / stages.length;
  let progress = 10;

  for (const stage of stages) {
    progress = Math.min(95, progress + step);
    setProgress(progress, stage.name, stage.details);
    await sleep(220);
  }

  completeProgress("Completed", "Answer generated and evidence retrieved.");
}

async function initializeFromHealth() {
  try {
    const res = await fetch("/api/health");
    if (!res.ok) {
      return;
    }
    const data = await res.json();
    if (!data.ok) {
      return;
    }

    if (data.index_ready) {
      hasIndex = true;
      docName.textContent = data.document_name || "Indexed document";
      chunkCount.textContent = "Ready";
      indexTime.textContent = "Loaded";
      setAppStatus("done", "Ready");
      setProgressState("done");
      setProgress(100, "Index ready", "An indexed document is available for chat.");
      showTimeline([
        {
          name: "Index restored",
          details: "Detected an already indexed document. Chat is ready.",
          duration_ms: undefined,
        },
      ]);
      appendMessage(
        "assistant",
        `Using indexed document: ${data.document_name}. Ask your questions.`
      );
    } else {
      setAppStatus("idle", "Idle");
      setProgressState("idle");
      setProgress(0, "Idle", "Upload a document to start processing.");
    }
  } catch (_error) {
    // Ignore health bootstrap failures and let manual upload continue working.
  }
}

function showTimeline(items) {
  if (!items || !items.length) {
    timeline.innerHTML = `<article class="timeline-item muted"><div class="dot"></div><div><h4>No events</h4><p>Processing events will appear here.</p></div></article>`;
    return;
  }

  timeline.innerHTML = items
    .map(
      (item) => `
      <article class="timeline-item">
        <div class="dot"></div>
        <div>
          <h4>${item.name}</h4>
          <p>${item.details}</p>
          ${item.duration_ms !== undefined ? `<p class="meta">${item.duration_ms} ms</p>` : ""}
        </div>
      </article>
    `
    )
    .join("");
}

function appendMessage(role, text) {
  const bubble = document.createElement("div");
  bubble.className = `bubble ${role}`;
  bubble.textContent = text;
  chatLog.appendChild(bubble);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function renderRetrieval(items) {
  if (!items || !items.length) {
    retrievalList.innerHTML = '<p class="muted-copy">No retrieval results yet.</p>';
    return;
  }

  retrievalList.innerHTML = items
    .map(
      (row) => `
      <article class="retrieval-item">
        <h4>Rank ${row.rank} · Chunk ${row.chunk_id} · Score ${row.score.toFixed(3)}</h4>
        <p>${row.snippet}</p>
        ${row.expansion_terms && row.expansion_terms.length ? `<p class="meta">Expansion: ${row.expansion_terms.join(", ")}</p>` : ""}
      </article>
    `
    )
    .join("");
}

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = fileInput.files?.[0];
  if (!file) {
    alert("Please select a file first.");
    return;
  }

  const form = new FormData();
  form.append("file", file);

  uploadBtn.disabled = true;
  startProgressSimulation({
    label: "Uploading and indexing",
    hint: "Extracting text and preparing SIRA expansion terms.",
    start: 8,
    max: 86,
    step: 3,
    intervalMs: 280,
  });

  showTimeline([
    {
      name: "Uploading document",
      details: "Sending file to server and starting SIRA offline expansion.",
      duration_ms: undefined,
    },
  ]);

  try {
    const res = await fetch("/api/upload", {
      method: "POST",
      body: form,
    });

    const data = await res.json();
    if (!res.ok || !data.ok) {
      throw new Error(data.detail || data.message || "Upload failed");
    }

    hasIndex = true;
    docName.textContent = data.index.document_name;
    chunkCount.textContent = data.index.chunk_count;
    indexTime.textContent = `${data.index.elapsed_ms} ms`;
    completeProgress(
      "Index complete",
      `Document indexed with ${data.index.chunk_count} chunks in ${data.index.elapsed_ms} ms.`
    );

    showTimeline([
      {
        name: "Document parsed",
        details: "Extracted text and prepared chunks.",
        duration_ms: undefined,
      },
      {
        name: "Offline expansion",
        details: "Generated related lexical terms for each chunk.",
        duration_ms: undefined,
      },
      {
        name: "BM25 index built",
        details: `Created searchable index with ${data.index.chunk_count} chunks.`,
        duration_ms: data.index.elapsed_ms,
      },
    ]);

    appendMessage(
      "assistant",
      `Document indexed: ${data.index.document_name}. You can now ask questions.`
    );
  } catch (error) {
    failProgress(error.message || "Upload failed.");
    showTimeline([
      {
        name: "Upload failed",
        details: error.message,
        duration_ms: undefined,
      },
    ]);
  } finally {
    uploadBtn.disabled = false;
  }
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = messageInput.value.trim();
  if (!message) {
    return;
  }
  if (!hasIndex) {
    alert("Please upload and index a document first.");
    return;
  }

  appendMessage("user", message);
  messageInput.value = "";
  sendBtn.disabled = true;

  startProgressSimulation({
    label: "Running SIRA query",
    hint: "Expected sketch, fusion, retrieval, then answer synthesis.",
    start: 10,
    max: 84,
    step: 2,
    intervalMs: 220,
  });

  showTimeline([
    {
      name: "Running SIRA query flow",
      details: "Expected sketch -> query fusion -> one-shot BM25 retrieval -> answer.",
      duration_ms: undefined,
    },
  ]);

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, top_k: 4 }),
    });

    const data = await res.json();
    if (!res.ok || !data.ok) {
      throw new Error(data.detail || "Chat failed");
    }

    appendMessage("assistant", data.answer || "No answer returned.");
    showTimeline(data.stages || []);
    renderRetrieval(data.retrieved || []);
    await replayStageProgress(data.stages || []);
  } catch (error) {
    appendMessage("assistant", `Error: ${error.message}`);
    failProgress(error.message || "Chat failed.");
    showTimeline([
      {
        name: "Chat failed",
        details: error.message,
        duration_ms: undefined,
      },
    ]);
  } finally {
    sendBtn.disabled = false;
  }
});

initializeFromHealth();
