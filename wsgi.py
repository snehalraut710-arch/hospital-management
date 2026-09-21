"""Entry point.

    flask --app wsgi init-db
    flask --app wsgi seed
    flask --app wsgi train-model
    flask --app wsgi run --debug

or simply: python wsgi.py
"""
from hospital import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
