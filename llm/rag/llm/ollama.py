import subprocess


class OllamaLLM:
    def __init__(self, model, temperature=None):
        self.model = model

    def generate(self, prompt: str) -> str:
        proc = subprocess.Popen(
            ["ollama", "run", self.model],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        stdout, stderr = proc.communicate(prompt)

        if proc.returncode != 0:
            raise RuntimeError(f"Ollama error: {stderr}")

        return stdout.strip()
