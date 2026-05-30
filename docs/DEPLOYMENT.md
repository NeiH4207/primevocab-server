# AIFOREN API Deployment Guide

This guide covers different deployment methods for the AIFOREN API.

## Table of Contents

- [Local Development](#local-development)
- [Docker Deployment](#docker-deployment)
- [Railway Deployment](#railway-deployment)
- [Production Deployment](#production-deployment)
- [Environment Configuration](#environment-configuration)
- [Database Setup](#database-setup)
- [Monitoring and Logging](#monitoring-and-logging)

## Local Development

### Prerequisites

- Python 3.12+
- MySQL 8.0+
- MongoDB 4.4+
- Redis 6.0+ (optional)

### Setup Steps

1. **Clone and setup environment**
   ```bash
   git clone https://github.com/your-org/aiforen-api.git
   cd aiforen-api
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your local settings
   ```

3. **Initialize database**
   ```bash
   mysql -u root -p < database_schema.sql
   ```

4. **Run the application**
   ```bash
   python main.py
   ```

The API will be available at `http://localhost:8000`

## Docker Deployment

### Build and Run

1. **Build the Docker image**
   ```bash
   docker build -t aiforen-api .
   ```

2. **Run with Docker Compose (recommended)**
   ```bash
   docker-compose up -d
   ```

3. **Or run standalone**
   ```bash
   docker run -d \
     --name aiforen-api \
     -p 8000:8000 \
     --env-file .env \
     aiforen-api
   ```

### Docker Compose Configuration

```yaml
version: '3.8'

services:
  api:
    build: .
    ports:
      - "8000:8000"
    environment:
      - ENVIRONMENT=production
      - DEBUG=false
    env_file:
      - .env
    depends_on:
      - mysql
      - mongodb
      - redis

  mysql:
    image: mysql:8.0
    environment:
      MYSQL_ROOT_PASSWORD: ${MYSQL_PASSWORD}
      MYSQL_DATABASE: ${MYSQL_DATABASE}
      MYSQL_USER: ${MYSQL_USER}
      MYSQL_PASSWORD: ${MYSQL_PASSWORD}
    volumes:
      - mysql_data:/var/lib/mysql
      - ./database_schema.sql:/docker-entrypoint-initdb.d/schema.sql
    ports:
      - "3306:3306"

  mongodb:
    image: mongo:4.4
    environment:
      MONGO_INITDB_ROOT_USERNAME: ${MONGO_USER}
      MONGO_INITDB_ROOT_PASSWORD: ${MONGO_PASSWORD}
    volumes:
      - mongodb_data:/data/db
    ports:
      - "27017:27017"

  redis:
    image: redis:6.0-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data

volumes:
  mysql_data:
  mongodb_data:
  redis_data:
```

## Railway Deployment

PrimeVocab production uses **Railway** for the API (Postgres, MongoDB, Redis) and **Vercel** for the Next.js frontend.

See **[RAILWAY.md](./RAILWAY.md)** for:

- Required environment variables (`APP_ENV`, `CORS_ORIGINS`, `DATABASE_URL`, Google OAuth, etc.)
- Health check path (`/api/v1/health`)
- CORS verification for https://primevocab.com

Deploy from the repo root:

```bash
railway link   # project helpful-learning, service py-server
railway up -d -s py-server -e production
```

### Auto-deploy on merge to `main`

GitHub Actions workflow: `.github/workflows/deploy-production.yml`.  
Secrets: see **[AUTO_DEPLOY.md](./AUTO_DEPLOY.md)**.

## Production Deployment

### Server Requirements

- Ubuntu 20.04+ or CentOS 8+
- Python 3.12+
- Nginx (reverse proxy)
- MySQL 8.0+
- MongoDB 4.4+
- Redis 6.0+
- SSL certificate

### Production Setup

1. **Server setup**
   ```bash
   # Update system
   sudo apt update && sudo apt upgrade -y

   # Install dependencies
   sudo apt install python3.12 python3.12-venv nginx mysql-server mongodb redis-server
   ```

2. **Application deployment**
   ```bash
   # Create app user
   sudo useradd -m -s /bin/bash aiforen
   sudo su - aiforen

   # Clone and setup
   git clone https://github.com/your-org/aiforen-api.git
   cd aiforen-api
   python3.12 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Configure systemd service**
   ```bash
   sudo nano /etc/systemd/system/aiforen-api.service
   ```

   ```ini
   [Unit]
   Description=AIFOREN API
   After=network.target

   [Service]
   Type=exec
   User=aiforen
   Group=aiforen
   WorkingDirectory=/home/aiforen/aiforen-api
   Environment=PATH=/home/aiforen/aiforen-api/venv/bin
   ExecStart=/home/aiforen/aiforen-api/venv/bin/python main.py
   Restart=always
   RestartSec=10

   [Install]
   WantedBy=multi-user.target
   ```

4. **Configure Nginx**
   ```bash
   sudo nano /etc/nginx/sites-available/aiforen-api
   ```

   ```nginx
   server {
       listen 80;
       server_name your-domain.com;

       location / {
           proxy_pass http://127.0.0.1:8000;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }
   ```

5. **Enable and start services**
   ```bash
   sudo systemctl enable aiforen-api
   sudo systemctl start aiforen-api
   sudo systemctl enable nginx
   sudo systemctl start nginx
   sudo ln -s /etc/nginx/sites-available/aiforen-api /etc/nginx/sites-enabled/
   sudo nginx -t && sudo systemctl reload nginx
   ```

### SSL Configuration

Use Let's Encrypt for free SSL:

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

## Environment Configuration

### Required Environment Variables

```env
# Application
APP_NAME=AIFOREN API
VERSION=2.0.0
ENVIRONMENT=production
DEBUG=false
HOST=0.0.0.0
PORT=8000

# Database
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=aiforen_user
MYSQL_PASSWORD=secure_password
MYSQL_DATABASE=aiforen_db

MONGODB_URL=mongodb://localhost:27017
MONGODB_DB_NAME=aiforen_mongo

REDIS_URL=redis://localhost:6379/0

# Security
SECRET_KEY=very-secure-secret-key-change-this
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7

# OAuth
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret

# AI Services
OPENAI_API_KEY=your-openai-key
ANTHROPIC_API_KEY=your-anthropic-key

# AWS
AWS_ACCESS_KEY_ID=your-aws-key
AWS_SECRET_ACCESS_KEY=your-aws-secret
S3_BUCKET_NAME=your-s3-bucket

# Payments
PAYOS_API_KEY=your-payos-key
PAYOS_CHECKSUM_KEY=your-checksum-key
PAYOS_CLIENT_ID=your-client-id

# CORS
CORS_ORIGINS=["https://your-frontend-domain.com"]
```

## Database Setup

### MySQL Setup

1. **Create database and user**
   ```sql
   CREATE DATABASE aiforen_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   CREATE USER 'aiforen_user'@'localhost' IDENTIFIED BY 'secure_password';
   GRANT ALL PRIVILEGES ON aiforen_db.* TO 'aiforen_user'@'localhost';
   FLUSH PRIVILEGES;
   ```

2. **Import schema**
   ```bash
   mysql -u aiforen_user -p aiforen_db < database_schema.sql
   ```

### MongoDB Setup

1. **Create database and user**
   ```javascript
   use aiforen_mongo
   db.createUser({
     user: "aiforen_user",
     pwd: "secure_password",
     roles: [{ role: "readWrite", db: "aiforen_mongo" }]
   })
   ```

## Monitoring and Logging

### Health Checks

The API provides several health check endpoints:

- `GET /health` - Basic health check
- `GET /health/database` - Database connectivity
- `GET /api/v1/health` - API health with detailed info

### Logging Configuration

Set logging level in environment:
```env
LOG_LEVEL=INFO
LOG_FILE=/var/log/aiforen/api.log
```

### Monitoring with Prometheus

Add metrics endpoint and configure monitoring:

```python
# In your application
from prometheus_client import Counter, Histogram, generate_latest

REQUEST_COUNT = Counter('http_requests_total', 'Total HTTP requests')
REQUEST_LATENCY = Histogram('http_request_duration_seconds', 'HTTP request latency')
```

### Log Rotation

Configure logrotate:
```bash
sudo nano /etc/logrotate.d/aiforen-api
```

```
/var/log/aiforen/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    postrotate
        systemctl reload aiforen-api
    endscript
}
```

## Backup and Recovery

### Database Backups

**MySQL**
```bash
# Daily backup
mysqldump -u aiforen_user -p aiforen_db > backup_$(date +%Y%m%d).sql

# Restore
mysql -u aiforen_user -p aiforen_db < backup_20231201.sql
```

**MongoDB**
```bash
# Backup
mongodump --db aiforen_mongo --out ./backup_$(date +%Y%m%d)

# Restore
mongorestore --db aiforen_mongo ./backup_20231201/aiforen_mongo
```

## Troubleshooting

### Common Issues

1. **Database connection errors**
   - Check database credentials
   - Verify database is running
   - Check firewall settings

2. **Import errors**
   - Ensure virtual environment is activated
   - Check Python version compatibility
   - Verify all dependencies are installed

3. **Permission errors**
   - Check file permissions
   - Ensure correct user ownership
   - Verify systemd service configuration

### Log Analysis

```bash
# Check application logs
sudo journalctl -u aiforen-api -f

# Check Nginx logs
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log

# Check system resources
htop
df -h
free -h
```

## Security Checklist

- [ ] Use HTTPS in production
- [ ] Set strong SECRET_KEY
- [ ] Configure firewall (UFW/iptables)
- [ ] Regular security updates
- [ ] Database password security
- [ ] API rate limiting
- [ ] Input validation
- [ ] Error handling (no sensitive info exposure)
- [ ] Regular backups
- [ ] Monitor access logs

---

For additional support, contact: devops@aiforen.com
