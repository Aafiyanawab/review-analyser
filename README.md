                    # 📊 Review Analyser

        ### Cloud-Native Customer Review Sentiment Analysis Platform

    ![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
    ![Flask](https://img.shields.io/badge/Flask-000000?style=for-the-badge&logo=flask&logoColor=white)
    ![AWS](https://img.shields.io/badge/AWS-232F3E?style=for-the-badge&logo=amazonaws&logoColor=FF9900)
    ![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)
    ![Amazon EC2](https://img.shields.io/badge/Amazon_EC2-FF9900?style=for-the-badge&logo=amazonec2&logoColor=white)
    ![Amazon S3](https://img.shields.io/badge/Amazon_S3-569A31?style=for-the-badge&logo=amazons3&logoColor=white)
    ![DynamoDB](https://img.shields.io/badge/DynamoDB-4053D6?style=for-the-badge&logo=amazondynamodb&logoColor=white)

---

## 🌐 Live Demo

**Website:** http://reviewanalyser.site

---

## 📌 About

Review Analyser is a cloud-native sentiment analysis platform that enables businesses to analyze customer reviews using AWS AI services.

Users can upload CSV and Excel files containing customer reviews. The application automatically processes reviews, performs sentiment analysis using Amazon Comprehend, stores uploaded files in Amazon S3, saves results in DynamoDB, and visualizes insights through an interactive dashboard.

The application is containerized using Docker and deployed on AWS EC2.

---

## ✨ Features

- Upload CSV, XLSX and XLS review files
- AI-powered sentiment analysis using Amazon Comprehend
- Interactive analytics dashboard
- Sentiment distribution charts
- Confidence score analysis
- DynamoDB result storage
- Amazon S3 file storage
- Docker containerization
- AWS EC2 deployment
- Analysis history tracking

---

## 🛠️ Tech Stack

| Layer | Technology |
|---------|------------|
| Backend | Python, Flask |
| Frontend | HTML, CSS, JavaScript |
| AI Service | Amazon Comprehend |
| Database | Amazon DynamoDB |
| Storage | Amazon S3 |
| Cloud Platform | Amazon EC2 |
| Containerization | Docker |

---
## 📂 Project Structure

```text
review-analyser/
│
├── app.py
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
│
├── lambda/
│   └── AWS Lambda Functions
│
├── templates/
│   └── Frontend Templates
│
├── screenshots/
│   ├── upload.png
│   ├── dashboard.png
│   └── results.png
│
└── README.md
```
## 📸 Screenshots

### Upload Reviews

![Upload Screen](screenshots/upload.png)

### Dashboard Analytics

![Dashboard](screenshots/dashboard.png)

### Analysis Results

![Results](screenshots/results.png)

---

## ☁️ AWS Services Used

- Amazon EC2
- Amazon S3
- Amazon DynamoDB
- Amazon Comprehend
- AWS Lambda
- Docker

---

## 🚀 Local Setup

### Clone Repository

```bash
git clone https://github.com/YOUR_USERNAME/review-analyser.git

cd review-analyser
```

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Configure Environment Variables

Create a `.env` file:

```env
AWS_ACCESS_KEY=
AWS_SECRET_KEY=
AWS_REGION=us-east-1

DYNAMODB_TABLE=review-results

S3_BUCKET=

SESSION_SECRET=

DATA_TTL_HOURS=24
```

### Run Application

```bash
python app.py
```

---

## 🐳 Docker Deployment

```bash
docker compose up -d --build
```

Verify:

```bash
docker ps
```

---


---

## 🔮 Future Improvements

- 👤 User authentication and role-based access
- 📄 Export sentiment reports as PDF
- 📧 Email-based report delivery
- 📊 Advanced analytics and trend visualization
- 🌍 Multi-language sentiment analysis
- 📈 Real-time sentiment monitoring dashboard

## 👩‍💻 Author

Aafiya Nawab

---

## 📄 License

This project is open source and available under the MIT License