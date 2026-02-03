async function generate() {
  const prompt = document.getElementById("prompt").value;
  const status = document.getElementById("status");
  const img = document.getElementById("result");

  if (!prompt.trim()) return;

  status.textContent = "⏳ Generating (this may take a minute)...";
  img.style.display = "none";

  try {
    const res = await fetch("/api/generate-image", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt })
    });

    const data = await res.json();
    img.src = data.image_url + "?t=" + Date.now();
    img.onload = () => img.style.display = "block";
    status.textContent = "✅ Done";

  } catch (e) {
    status.textContent = "❌ Generation failed";
  }
}
