import express from "express";

const app = express();
app.use(express.json());
const OLLAMA_URL = process.env.OLLAMA_URL || "http://host.docker.internal:11434";
const OLLAMA_MODEL = process.env.OLLAMA_MODEL || "qwen2.5:7b-instruct-q2_K";

app.get("/", (req, res) => {
  res.send("VERSION 2");
});

app.get("/health", (req, res) => {
  res.json({ status: "ok" });
});

function buildPrompt(engineData, userLevel) {
  const prompt = `
You are a chess coach.

Return ONLY valid JSON.
DO NOT include any explanation text.
DO NOT wrap JSON in quotes.
DO NOT add "Here is the JSON".

Format EXACTLY:

{
  "mistake": "...",
  "consequence": "...",
  "better_move": "...",
  "category": "...",
  "severity": "blunder | mistake | inaccuracy"
}

Rules:
- max 20 words per field
- simple language
- no line breaks inside values

Engine data:
${JSON.stringify(engineData)}

User level: ${userLevel}
`;

  return prompt;
}

async function generateCoachResponse(engineData, userLevel) {
  const prompt = buildPrompt(engineData, userLevel);

  const response = await fetch(`${OLLAMA_URL}/api/generate`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      model: OLLAMA_MODEL,
      prompt,
      stream: false
    })
  });

  const responseText = await response.text();

  if (!response.ok) {
    const trimmed = responseText.slice(0, 500);
    throw new Error(`Ollama upstream ${response.status}: ${trimmed}`);
  }

  let data;
  try {
    data = JSON.parse(responseText);
  } catch {
    data = { response: responseText };
  }

  const raw = data.response || "";
  let parsed;

  try {
    // Extract JSON from text (even if LLM adds extra text)
    const jsonMatch = raw.match(/\{[\s\S]*\}/);

    if (!jsonMatch) throw new Error("No JSON found");

    parsed = JSON.parse(jsonMatch[0]);
  } catch (e) {
    console.log("Parsing failed:", raw);

    parsed = {
      mistake: raw,
      consequence: "",
      better_move: "",
      category: "unknown",
      severity: "unknown"
    };
  }

  return {
    mistake: parsed.mistake || "",
    consequence: parsed.consequence || "",
    better_move: parsed.better_move || "",
    category: parsed.category || "general",
    severity: parsed.severity || "inaccuracy"
  };
}

app.post("/coach", async (req, res) => {
  const { engineData, userLevel } = req.body || {};

  try {
    const result = await generateCoachResponse(engineData || {}, userLevel || "intermediate");
    res.json(result);
  } catch (err) {
    console.error(err);
    res.status(502).json({ error: "LLM upstream failed", detail: String(err.message || err) });
  }
});

app.post("/explain", async (req, res) => {
  const body = req.body || {};
  const engineData = body.engineData || body.stockfish_json || {};
  const userLevel = body.userLevel || "intermediate";

  try {
    const result = await generateCoachResponse(engineData, userLevel);
    res.json(result);
  } catch (err) {
    console.error(err);
    res.status(502).json({ error: "LLM upstream failed", detail: String(err.message || err) });
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
});
