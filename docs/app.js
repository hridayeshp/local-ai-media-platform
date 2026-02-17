const runCmd = `cd /Users/hridayeshpandit/Coding/local-ai-media-platform
docker compose up --build -d`;

function setFeedback(message) {
  const node = document.getElementById("copyFeedback");
  if (!node) return;
  node.textContent = message;
}

async function copyRunCommand() {
  try {
    await navigator.clipboard.writeText(runCmd);
    setFeedback("Run command copied.");
  } catch {
    setFeedback("Copy failed. Select the command block manually.");
  }
}

window.addEventListener("load", () => {
  const btn = document.getElementById("copyRunCmd");
  if (btn) btn.addEventListener("click", copyRunCommand);
});
