# 💊 Meditrack – Medical Inventory & Demand Forecasting System

Meditrack is a web-based **Medical Inventory and Pharmacy Management System** designed to help pharmacies manage medicines, inventory, customers, billing, sales, suppliers, and stock-related activities from a centralized application.

The system also includes **demand forecasting and recommendation features** to help pharmacies make better inventory decisions and reduce the possibility of stock shortages and overstocking.

---

## 📌 Project Overview

Managing pharmacy inventory manually can be time-consuming and can lead to problems such as:

- Medicine stock shortages
- Overstocking
- Expired medicines
- Difficulty tracking sales
- Manual billing processes
- Lack of sales analysis
- Difficulty predicting future medicine demand

**Meditrack** addresses these problems through a centralized web application that combines pharmacy management features with data-driven demand forecasting.

---

## ✨ Key Features

### 📊 Admin Dashboard

- Centralized pharmacy dashboard
- Inventory overview
- Sales-related information
- Staff management
- Performance monitoring
- AI assistant access

### 💊 Inventory Management

- Add and edit medicines
- View available inventory
- Track medicine quantities
- Monitor expiry dates
- Identify expiring medicines
- Supplier management
- Purchase order management
- Reorder suggestions
- Wastage reporting

### 📈 Demand Forecasting

- Analyze historical sales data
- Forecast future medicine demand
- Generate inventory recommendations
- Forecast accuracy analysis
- Support for sales data import
- Data-driven reorder recommendations

### 🧠 AI Assistant

- AI-assisted pharmacy management functionality
- AI tools integrated into the application
- Assistance with inventory-related operations

### 🧾 Billing System

- Create customer bills
- Generate invoices
- View billing history
- Handle returns
- Maintain return history
- Generate PDF invoices

### 👥 Customer Management

- Add new customers
- View customer list
- Manage customer information
- View customer profiles

### 📦 Data Management

- Import sales data using CSV
- Export information to Excel
- Generate PDF invoices and reports
- Database migration scripts
- Demo data generation scripts

### 🔐 Authentication & Access Control

- User authentication
- Role-based access
- Admin functionality
- Cashier functionality
- Protected application routes
- Custom 403 and 404 error pages

---

## 🛠️ Technologies Used

### Backend

- Python
- Flask
- Flask Blueprints
- SQLAlchemy

### Frontend

- HTML5
- CSS3
- JavaScript
- Jinja2 Templates

### Data & Analytics

- Python
- Pandas
- NumPy
- Demand Forecasting
- Sales Data Analysis

### Visualization

- Chart.js

### File Processing

- CSV
- Excel
- PDF

### Development Tools

- Git
- GitHub
- Python Virtual Environment

---

## 🏗️ Project Structure

```text
meditrack-medical-inventory/
│
├── app/
│   ├── admin/
│   │   ├── __init__.py
│   │   └── routes.py
│   │
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── routes.py
│   │   └── tools.py
│   │
│   ├── auth/
│   │   ├── __init__.py
│   │   └── routes.py
│   │
│   ├── cashier/
│   │   ├── __init__.py
│   │   └── routes.py
│   │
│   ├── customers/
│   │   ├── __init__.py
│   │   └── routes.py
│   │
│   ├── forecasting/
│   │   ├── __init__.py
│   │   ├── engine.py
│   │   └── recommend.py
│   │
│   ├── inventory/
│   │   ├── __init__.py
│   │   └── routes.py
│   │
│   ├── utils/
│   │   ├── csv_import.py
│   │   ├── excel_export.py
│   │   └── pdf_invoice.py
│   │
│   ├── static/
│   │   ├── css/
│   │   └── vendor/
│   │
│   ├── templates/
│   │   ├── admin/
│   │   ├── auth/
│   │   ├── cashier/
│   │   ├── customers/
│   │   ├── errors/
│   │   └── inventory/
│   │
│   ├── config.py
│   ├── decorators.py
│   ├── extensions.py
│   └── models.py
│
├── data/
│   └── dailysales_import.csv
│
├── instance/
│   └── .gitkeep
│
├── uploads/
│   └── .gitkeep
│
├── backfill_bills_from_history.py
├── migrate_add_generic_name.py
├── migrate_add_returns_and_accuracy.py
├── requirements.txt
├── run.py
├── seed_demo_data.py
├── seed_real_data.py
├── seed_two_month_demo.py
├── .env.example
├── .gitignore
└── README.md
