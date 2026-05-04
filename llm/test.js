import http from 'k6/http';

export default function () {
  http.post('http://localhost:8000/explain', JSON.stringify({
    fen: "startpos"
  }), {
    headers: { 'Content-Type': 'application/json' }
  });
}
