import os
def release_port(port):
    """Forcefully release a port."""
    print(f"Releasing port {port}...")
    
    if os.name == "posix":
        os.system(f"lsof -ti:{port} | xargs -r kill -9")
        print(f"Port {port} released.")
