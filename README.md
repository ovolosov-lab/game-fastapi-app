# Guess the Word Game
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg) ![Python](https://img.shields.io/badge/Python-3.11.5-3776ab?logo=python&logoColor=white) ![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791?logo=postgresql&logoColor=white)

A simple semantic game project using FastAPI + pgvector: a web application built using FastAPI and PostgreSQL + pgvector
and the sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 model, fully containerized using Docker.

This project was born in the process of studying the aspects of using language models in Python programs, that is, 
it can be said that this is an educational project.
I would be delighted if you'd try this game and share your feedback, bug reports, or suggestions for new features!

## ✨ Key Features
- **Hot and cold:** This is a "Hot and Cold" style game - Depending on the semantic similarity between the secret word and the player's guess, their answer is colored 
                     in the corresponding "temperature" color, and the similarity percentage is displayed.
- **Many players:** Multiple users participating in the game at the same time
- **Continuous cycle of games:** Games follow one another continuously. Each game lasts 1 hour or until one player guesses the word. A new game is created immediately.
- **Tips:** Several types of hints are available in games: the first and last letters of a word, similar words - analogs, 
            and a description of the word from Artificial Intelligence. 
- **Radar:** Additionally, the player's progress is reflected on the radar. 
             Under the radar, humorous messages from the "ship's computer" are "printed" in the style of popular science fiction films. 
- **Internationalization:** The player can choose one of three interface languages: English, French and Russian
- **Secret words:** Three sets of common nouns and their embeddings are uploaded to the Database: English, French and Russian words. 
- **Secret word language:** The language in which the secret word will be given in the next game is determined by the interface language chosen by the first player in the current game.
- **Language learning assistance** The player can guess the secret word in a language they are more comfortable with, different from the language in which the word was originally guessed.
                                    However, the game is designed to help players expand their vocabulary while learning English or French.
- **Demonstration of achievements:** The winner is awarded points based on the word length and the clues used. These points are stored and displayed in the "Game Statistics" section.
- **User Authentication:** Authentication using JWT tokens.

## 🛠 Technology Stack
- **Language:** Python 3.11.5 (Slim)
- **Framework:** FastAPI + SQLAlchemy (Async)
- **Database:** PostgreSQL 16 + pgvector
- **Models:** sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2  and  GLM-5.2
- **Containerization:** Docker & Docker Compose 

## 🚀 Quick Start

To run the project, you need to have [Docker](https://docker.com) installed.

1. **Clone the repository:**
   ```bash
   git clone https://github.com
   cd project-name   ```

2. **Configure Environment Variables:**
   Create a `.env` file based on the provided template:
   ```bash
   cp .env.example .env
   ```
   *Note: Don't forget to fill in your real credentials in the `.env` file!*

3. **Launch with Docker Compose:**
   ```bash
   docker-compose up --build
   ```

The application will be available at: [http://localhost:8000](http://localhost:8000)

## 📖 API Documentation
Once the app is running, you can explore the API here:
- **Swagger UI:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc:** [http://localhost:8000/redoc](http://localhost:8000/redoc)

## ⚙️ Environment Variables
The application requires the following variables (defined in your `.env`):
- `DB_USER` — Database username
- `DB_PASSWORD` — Database password
- `DB_NAME` — Database name
- `DB_HOST` — Database host (use `postgres_container` for Docker)
- `DB_PORT` — Database port (default: 5432)
- `JWT_SECRET` -  your_jwt_secret_key
- `AI_API_PATH` - https://api.z.ai/api/coding/paas/v4
- `AI_API_KEY` -  your api key to the model


## 🙏 Credits

This project is built using great open-source tools and models:

*   **[FastEmbed](https://github.com)** — A fast and lightweight library from the Qdrant team for generating embeddings. Distributed under the MIT license.
*   **[paraphrase-multilingual-MiniLM-L12-v2](https://huggingface.co)** — A multilingual model from the `sentence-transformers` community, providing excellent semantic accuracy. 
                                                                           Distributed under the Apache 2.0 license.
*   **[Hugging Face](https://huggingface.co)** — for hosting the model and providing infrastructure.


## 📄 License
This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.
