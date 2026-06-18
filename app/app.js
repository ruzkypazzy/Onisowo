// Àkànjí Oníṣòwò — PWA chat client
// Talks to the local backend (akanji-server on :8765) which wraps the bot's skills.

const API_BASE = localStorage.getItem("akanji.api") || `${location.protocol}//${location.hostname}:8765`;
const USER_ID = localStorage.getItem("akanji.userId") || crypto.randomUUID();
localStorage.setItem("akanji.userId", USER_ID);

const chat = document.getElementById("chat");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const send = document.getElementById("send");
const greeting = document.getElementById("greeting");

// Quick-action buttons
document.querySelectorAll(".quick-actions button").forEach((b) => {
  b.addEventListener("click", () => {
    const action = b.dataset.action;
    const prompts = {
      analyze: "Analyze the current market and suggest a pair to trade",
      balance: "What's my balance?",
      buy: "I want to buy some crypto. What's a good pair right now?",
      positions: "Show my open positions",
      autotrade: "Go with $5",
      skills: "What skills do you have?",
    };
    input.value = prompts[action] || "";
    form.requestSubmit();
  });
});

// Auto-resize textarea
input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 120) + "px";
  send.disabled = input.value.trim().length === 0;
});

input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

// Append a message bubble
function appendMsg(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  if (role === "bot") {
    const img = document.createElement("img");
    img.src = "/assets/profile_picture.png";
    img.alt = "Àkànjí";
    img.className = "mini-avatar";
    div.appendChild(img);
  }
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = formatText(text);
  div.appendChild(bubble);
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return bubble;
}

function appendThinking() {
  const div = document.createElement("div");
  div.className = "msg bot thinking-msg";
  const img = document.createElement("img");
  img.src = "/assets/profile_picture.png";
  img.className = "mini-avatar";
  div.appendChild(img);
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML =
    '<div class="thinking"><span></span><span></span><span></span></div>';
  div.appendChild(bubble);
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return bubble;
}

function formatText(s) {
  // Minimal Markdown for chat bubbles. Escape everything first.
  const esc = String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  // Bold **x**
  let out = esc.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  // Inline code `x`
  out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
  // Code blocks ```x```
  out = out.replace(/```([\s\S]*?)```/g, "<pre>$1</pre>");
  // Italics *x*
  out = out.replace(/(^|\W)\*([^*]+)\*(?=\W|$)/g, "$1<em>$2</em>");
  return out;
}

// Set greeting based on WAT timezone
function setGreeting() {
  const now = new Date();
  // WAT = UTC+1
  const wat = new Date(now.getTime() + (now.getTimezoneOffset() + 60) * 60000);
  const h = wat.getHours();
  let g;
  if (h >= 5 && h < 12) g = "Ọniṣọwọ́ ẹ káàrọ̀ ☀️";
  else if (h >= 12 && h < 16) g = "Ọniṣọwọ́ ẹ káàsán 🌤️";
  else if (h >= 16 && h < 19) g = "Ọniṣọwọ́ ẹ káàlẹ́ 🌇";
  else g = "Ọniṣọwọ́ ẹ káàlẹ́ òru 🌙";
  greeting.textContent = g;
}
setGreeting();
setInterval(setGreeting, 60_000);

// Send a message
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  input.style.height = "auto";
  send.disabled = true;
  appendMsg("user", text);
  const thinking = appendThinking();
  try {
    const r = await fetch(`${API_BASE}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: USER_ID, text }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    thinking.parentElement.remove();
    appendMsg("bot", data.reply || "(no response)");
  } catch (e) {
    thinking.parentElement.remove();
    appendMsg("bot", `❌ ${e.message || "Connection failed"}. Is the akanji server running?`);
  }
  send.disabled = false;
  input.focus();
});
