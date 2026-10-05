import argparse
import os


def main():
    parser = argparse.ArgumentParser(description="Start the Castwell gallery")
    parser.add_argument("--host", default="127.0.0.1", help="Listen address (default: loopback)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", default=os.getenv("CASTWELL_DATA_DIR", "data"))
    parser.add_argument("--model", default=None, help="Override the saved Whisper model name or local directory")
    args = parser.parse_args()
    os.environ["CASTWELL_DATA_DIR"] = args.data_dir
    if args.model is not None:
        os.environ["CASTWELL_TRANSCRIPTION_MODEL"] = args.model
    import uvicorn
    uvicorn.run("castwell.app:create_app", factory=True, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
