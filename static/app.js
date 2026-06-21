const urlInput = document.getElementById("url-input");
const detectTag = document.getElementById("detect-tag");
const pullBtn = document.getElementById("pull-btn");
const qualityPills = document.querySelectorAll(".pill");
const jobPanel = document.getElementById("job-panel");
const jobIdLabel = document.getElementById("job-id-label");
const jobState = document.getElementById("job-state");
const jobMeterFill = document.getElementById("job-meter-fill");
const jobMessage = document.getElementById("job-message");
const jobCounts = document.getElementById("job-counts");
const jobCountText = document.getElementById("job-count-text");
const jobResult = document.getElementById("job-result");
const downloadLink = document.getElementById("download-link");
const downloadFilename = document.getElementById("download-filename");
const jobError = document.getElementById("job-error");
const resetBtn = document.getElementById("reset-btn");

let selectedQuality = "best";
let pollTimer = null;
let detectDebounce = null;

qualityPills.forEach((pill) => {
  pill.addEventListener("click", () => {
    qualityPills.forEach((p) => p.classList.remove("active"));
    pill.classList.add("active");
    selectedQuality = pill.dataset.quality;
  });
});

urlInput.addEventListener("input", () => {
  clearTimeout(detectDebounce);
  const val = urlInput.value.trim();
  if (!val) {
    setDetectTag("idle", "—");
    return;
  }
  detectDebounce = setTimeout(() => checkUrl(val), 300);
});

function setDetectTag(kind, text) {
  detectTag.textContent = text;
  detectTag.classList.remove("is-video", "is-playlist", "is-invalid");
  if (kind === "video") detectTag.classList.add("is-video");
  if (kind === "playlist") detectTag.classList.add("is-playlist");
  if (kind === "invalid") detectTag.classList.add("is-invalid");
}

async function checkUrl(val) {
  try {
    const res = await fetch("/api/check-url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: val }),
    });
    const data = await res.json();
    if (data.type === "video") setDetectTag("video", "single video");
    else if (data.type === "playlist") setDetectTag("playlist", "playlist");
    else setDetectTag("invalid", "not recognized");
  } catch (e) {
    setDetectTag("invalid", "check failed");
  }
}

pullBtn.addEventListener("click", submitJob);
urlInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") submitJob();
});

async function submitJob() {
  const url = urlInput.value.trim();
  if (!url) {
    urlInput.focus();
    return;
  }

  pullBtn.disabled = true;
  jobPanel.hidden = false;
  jobResult.hidden = true;
  jobError.hidden = true;
  jobCounts.hidden = true;
  jobMeterFill.style.width = "0%";
  jobState.textContent = "submitting";
  jobState.classList.remove("state-done", "state-error");
  jobMessage.textContent = "Sending request...";
  jobIdLabel.textContent = "JOB —";

  try {
    const res = await fetch("/api/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, quality: selectedQuality }),
    });
    const data = await res.json();

    if (!res.ok) {
      showError(data.error || "Something went wrong submitting the job.");
      return;
    }

    jobIdLabel.textContent = `JOB ${data.job_id}`;
    pollStatus(data.job_id);
  } catch (e) {
    showError("Could not reach the server. Is the app running?");
  }
}

function pollStatus(jobId) {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/status/${jobId}`);
      const data = await res.json();
      renderStatus(data);

      if (data.status === "done" || data.status === "error") {
        clearInterval(pollTimer);
        pullBtn.disabled = false;
      }
    } catch (e) {
      clearInterval(pollTimer);
      showError("Lost connection while checking progress.");
      pullBtn.disabled = false;
    }
  }, 1200);
}

function renderStatus(data) {
  jobState.textContent = data.status.replace("_", " ");
  jobState.classList.remove("state-done", "state-error");

  jobMeterFill.style.width = `${data.progress || 0}%`;
  jobMessage.textContent = data.message || "";

  if (data.total_items > 1) {
    jobCounts.hidden = false;
    jobCountText.textContent = `${data.completed_items} / ${data.total_items} clips`;
  }

  if (data.status === "done") {
    jobState.classList.add("state-done");
    jobResult.hidden = false;
    downloadLink.href = `/downloads/${encodeURIComponent(data.output_path)}`;
    downloadFilename.textContent = data.output_path;
  }

  if (data.status === "error") {
    showError(data.error || "Unknown error.");
  }
}

function showError(msg) {
  jobState.textContent = "error";
  jobState.classList.add("state-error");
  jobError.hidden = false;
  jobError.textContent = msg;
  pullBtn.disabled = false;
}

resetBtn.addEventListener("click", () => {
  urlInput.value = "";
  setDetectTag("idle", "—");
  jobPanel.hidden = true;
  urlInput.focus();
});
