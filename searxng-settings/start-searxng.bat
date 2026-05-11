@echo off
REM SearXNG Agent Startup Script for Windows
REM Usage: start-searxng.bat [up|down|logs|status|update]

cd /d "%~dp0"

if "%1"=="" goto up
if "%1"=="up" goto up
if "%1"=="start" goto up
if "%1"=="down" goto down
if "%1"=="stop" goto down
if "%1"=="logs" goto logs
if "%1"=="status" goto status
if "%1"=="ps" goto status
if "%1"=="update" goto update
goto usage

:up
echo Starting SearXNG Agent...
docker-compose up -d
echo.
echo Waiting for health check (15s)...
timeout /t 15 /nobreak >nul
echo.
echo Verifying JSON API...
curl -s "http://localhost:8080/search?q=test&format=json" >nul 2>&1
if %errorlevel%==0 (
    echo [OK] SearXNG is running and JSON API is accessible
    echo.
    echo Test query:
    curl "http://localhost:8080/search?q=python+async+retry&format=json&engines=github_code,stackoverflow"
) else (
    echo [WARN] SearXNG may still be starting, try again in 30s
)
goto end

:down
echo Stopping SearXNG Agent...
docker-compose down
echo [OK] SearXNG stopped
goto end

:logs
echo Showing SearXNG logs...
docker-compose logs -f searxng
goto end

:status
echo SearXNG Agent Status:
docker-compose ps
echo.
docker-compose logs --tail=5 searxng
goto end

:update
echo Updating SearXNG Agent...
docker-compose pull
docker-compose up -d
echo [OK] SearXNG updated and restarted
goto end

:usage
echo SearXNG Agent Management Script
echo.
echo Usage: start-searxng.bat [command]
echo.
echo Commands:
echo   up, start   - Start SearXNG containers
echo   down, stop  - Stop SearXNG containers
echo   logs        - Show live logs
echo   status, ps  - Show container status
echo   update      - Pull latest images and restart
echo.
echo Default (no args): start containers
goto end

:end