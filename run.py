"""
PollyPilot Orchestrator
Launches FastAPI backend and Streamlit frontend with graceful shutdown.
"""
import os
import subprocess
import sys
import time

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    """Launch both services and manage lifecycle."""
    print("=" * 50)
    print("  PollyPilot - Polymarket Paper Trading Bot")
    print("=" * 50)
    print()

    processes = []

    try:
        # 1. Launch FastAPI backend
        print("[1/2] Starting FastAPI backend on port 8000...")
        fastapi_cmd = [
            sys.executable, "-m", "uvicorn",
            "server.main:app",
            "--host", "0.0.0.0",
            "--port", "8000",
        ]
        fastapi_proc = subprocess.Popen(
            fastapi_cmd,
            cwd=BASE_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        processes.append(("FastAPI", fastapi_proc))
        print(f"      PID: {fastapi_proc.pid}")

        # Wait for backend to be ready
        print("      Waiting for backend...")
        time.sleep(2)

        if fastapi_proc.poll() is not None:
            print("ERROR: FastAPI failed to start!")
            # Print any output
            out, _ = fastapi_proc.communicate(timeout=1)
            if out:
                print(out[:500])
            return 1

        print("      Backend ready!")
        print()

        # 2. Launch Streamlit frontend
        print("[2/2] Starting Streamlit dashboard on port 8501...")
        streamlit_cmd = [
            sys.executable, "-m", "streamlit",
            "run", "ui/dashboard.py",
            "--server.port", "8501",
            "--server.headless", "true",
            "--browser.gatherUsageStats", "false",
        ]
        streamlit_proc = subprocess.Popen(
            streamlit_cmd,
            cwd=BASE_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        processes.append(("Streamlit", streamlit_proc))
        print(f"      PID: {streamlit_proc.pid}")
        print()

        # Ready message
        print("=" * 50)
        print("  PollyPilot is running!")
        print()
        print("  FastAPI:   http://localhost:8000")
        print("  Swagger:   http://localhost:8000/docs")
        print("  Dashboard: http://localhost:8501")
        print()
        print("  Press Ctrl+C to stop")
        print("=" * 50)
        print()

        # Monitor processes
        while True:
            for name, proc in processes:
                if proc.poll() is not None:
                    print(f"\n{name} exited with code {proc.returncode}")
                    raise KeyboardInterrupt

            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\nShutting down...")

    finally:
        # Graceful shutdown
        for name, proc in processes:
            if proc.poll() is None:
                print(f"Stopping {name} (PID {proc.pid})...")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    print(f"Force killing {name}...")
                    proc.kill()

        print("Shutdown complete.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
