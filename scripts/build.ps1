# Build the React frontend for production, then start FastAPI to serve everything.
# Run from the project root: .\scripts\build.ps1

Write-Host "Building frontend..." -ForegroundColor Cyan
Set-Location frontend
npm run build
if ($LASTEXITCODE -ne 0) {
    Write-Host "Frontend build failed." -ForegroundColor Red
    Set-Location ..
    exit 1
}
Set-Location ..

Write-Host ""
Write-Host "Frontend built to frontend/dist/" -ForegroundColor Green
Write-Host ""
Write-Host "Start the server with:" -ForegroundColor Cyan
Write-Host "  python -m uvicorn src.delivery.api:app --host 0.0.0.0 --port 8000"
Write-Host ""
Write-Host "Then open http://localhost:8000 in your browser."
