Write-Host "Starting SECA server..."
Start-Process powershell -ArgumentList "uvicorn llm.server:app --reload"

Start-Sleep -Seconds 3

Write-Host "Checking health..."
Invoke-RestMethod http://127.0.0.1:8000/health

Write-Host "Testing move endpoint..."

$fen = "rn1qkbnr/pppb1ppp/3pp3/8/3PP3/5N2/PPP2PPP/RNBQKB1R w KQkq - 0 4"

Invoke-RestMethod http://127.0.0.1:8000/move `
  -Method POST `
  -ContentType "application/json" `
  -Body (@{fen=$fen} | ConvertTo-Json)
