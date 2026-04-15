import os
def release_port(port):
    """Forcefully release a port."""
    # Security: Ensure port is an integer to prevent command injection
    try:
        port = int(port)
    except (ValueError, TypeError):
        print(f"Error: Invalid port '{port}' provided to release_port.")
        return

    print(f"Releasing port {port}...")
    
    if os.name == "posix":
        os.system(f"lsof -ti:{port} | xargs -r kill -9")
        print(f"Port {port} released.")
