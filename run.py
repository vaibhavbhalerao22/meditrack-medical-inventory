from app import create_app

app = create_app()

if __name__ == "__main__":
    # debug=True is fine for local development; turn it off in production.
    app.run(debug=True, host="0.0.0.0", port=5000)
