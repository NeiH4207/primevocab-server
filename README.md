# AIFOREN API

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-Latest-green.svg)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen.svg)](https://github.com/pre-commit/pre-commit)

AI-powered IELTS learning platform backend API built with FastAPI, providing comprehensive writing assessment, payment processing, and user management capabilities.

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL 16+
- Redis 7+ (queues, dictionary cache, assessment streaming)

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/your-org/aiforen-api.git
   cd aiforen-api
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

5. **Start dependencies and migrate**
   ```bash
   docker compose up -d postgres redis
   alembic upgrade head
   python -m aiforen.scripts.seed
   ```

6. **Run the application**
   ```bash
   python main.py
   ```

   The API will be available at `http://localhost:8000`

## 📁 Project Structure

```
py-server/
├── aiforen/
│   ├── api/v1/endpoints/   # auth, learning (vocab), writing, payments, health
│   ├── app.py              # FastAPI factory
│   ├── core/               # config, db, deps, security, errors
│   ├── domain/             # SQLAlchemy + vocab domain logic
│   ├── integrations/       # LLM, payment, translate providers
│   ├── repositories/       # Postgres data access
│   ├── services/           # auth, learning, writing, payment, quota
│   └── workers/            # Redis stream assessment worker
├── alembic/                # Postgres migrations
├── docker-compose.yml      # postgres, redis, api, worker
├── main.py
└── requirements.txt
```

## 🔧 Configuration

The application uses environment variables for configuration. Key settings include:

### Database Configuration
```env
PG_HOST=localhost
PG_PORT=5432
PG_USER=aiforen
PG_PASSWORD=aiforen_dev
PG_DB=aiforen

REDIS_URL=redis://localhost:6379/0
```

### AI Services
```env
OPENAI_API_KEY=your-openai-key
ANTHROPIC_API_KEY=your-anthropic-key
GOOGLE_AI_API_KEY=your-google-ai-key
DEFAULT_AI_MODEL=openai
```

### Authentication
```env
SECRET_KEY=your-secret-key
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret
```

### Payment Processing
```env
PAYOS_API_KEY=your-payos-key
PAYOS_CHECKSUM_KEY=your-checksum-key
PAYOS_CLIENT_ID=your-client-id
```

## 🛠️ Development

### Code Quality

This project uses pre-commit hooks to ensure code quality:

```bash
# Install pre-commit hooks
pre-commit install

# Run hooks on all files
pre-commit run --all-files
```

The following tools are used:
- **Black**: Code formatting
- **isort**: Import sorting
- **flake8**: Linting
- **autoflake**: Remove unused imports

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=aiforen
```

### API Documentation

When running in development mode, interactive API documentation is available:
- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`

## 🌐 API Endpoints

### Authentication
- `POST /api/v1/auth/google` - Google OAuth authentication
- `POST /api/v1/auth/refresh` - Refresh access token
- `GET /api/v1/auth/me` - Get current user profile

### Writing Tasks
- `GET /api/v1/writing/tasks` - List writing tasks
- `POST /api/v1/writing/submit` - Submit writing task
- `GET /api/v1/writing/history` - Get submission history
- `POST /api/v1/writing/evaluate` - Get AI evaluation

### Payments
- `POST /api/v1/payment/payos/create` - Create payment
- `GET /api/v1/payment/plans` - Get pricing plans
- `POST /api/v1/payment/webhook` - Payment webhook

### Health Monitoring
- `GET /health` - Basic health check
- `GET /health/database` - Database health check
- `GET /api/v1/health` - API health check

## 🐳 Docker Deployment

### Build and run with Docker

```bash
# Build image
docker build -t aiforen-api .

# Run container
docker run -p 8000:8000 --env-file .env aiforen-api
```

### Deploy to Railway

Production API: `https://py-server-production.up.railway.app/api/v1`

See **[docs/RAILWAY.md](docs/RAILWAY.md)** for environment variables, CORS, and Google OAuth setup.

## 📊 Monitoring and Logging

The application includes comprehensive monitoring:

- **Health Checks**: Multiple endpoints for system health
- **Request Logging**: Automatic logging of all requests
- **Performance Metrics**: Response time tracking
- **Error Tracking**: Comprehensive error logging

## 🔒 Security Features

- **JWT Authentication**: Secure token-based authentication
- **OAuth2 Integration**: Google OAuth support
- **CORS Configuration**: Proper cross-origin setup
- **Input Validation**: Comprehensive request validation
- **Rate Limiting**: API rate limiting support
- **Security Headers**: Standard security headers

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests and pre-commit hooks
5. Commit your changes (`git commit -m 'Add amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

### Development Guidelines

- Follow PEP 8 style guide
- Write comprehensive tests
- Document all public APIs
- Use type hints
- Keep functions small and focused

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🆘 Support

For support and questions:

- 📧 Email: support@aiforen.com
- 📖 Documentation: `/docs`
- 🐛 Issues: GitHub Issues

## 🏗️ Architecture

The application follows a clean architecture pattern:

- **API Layer**: FastAPI routers and endpoints
- **Service Layer**: Business logic and orchestration
- **Core Layer**: Cross-cutting concerns (config, security, database)
- **Models Layer**: Data models and schemas
- **Modules Layer**: Feature-specific functionality

## 🚀 Recent Updates

- ✅ Restructured codebase for better maintainability
- ✅ Added comprehensive pre-commit hooks
- ✅ Improved API documentation
- ✅ Enhanced security features
- ✅ Added health monitoring service
- ✅ Optimized database connections

---

**Built with ❤️ by the AIFOREN Team**
