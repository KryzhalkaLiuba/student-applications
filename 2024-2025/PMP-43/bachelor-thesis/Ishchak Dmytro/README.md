# D-MSIR mini 🇺🇦  
*Затримкова компартментна модель поширення COVID-19 в Україні*

[![pytest](https://img.shields.io/badge/tests-passing-green)](./tests) 
[![license](https://img.shields.io/badge/license-MIT-blue)](./LICENSE)

---

## 1. Короткий опис  
`dmsir-mini` — компактний, але повноцінний приклад GitHub-репозиторію для епідемічного моделювання.  
Ядро реалізує **D-MSIR**-систему з одною дискретною затримкою τ та зниженим коефіцієнтом заразності θ для частково імунізованих. Репозиторій містить:

| Модуль | Файл | Призначення |
|--------|------|-------------|
| **Модель** | `dmsir_core/dmsir_dde.py` | Runge–Kutta 4(5) (`scipy.ode`) + інтерполяція лагу |
| **REST API** | `dmsir_api/main.py` | FastAPI `/forecast` → JSON-траєкторія |
| **UI** | `dmsir_ui/Home.py` | Streamlit-дашборд з інтерактивними слайдерами |
| **Тести** | `tests/test_mass.py` | Перевірка збереження маси компартментів |
| **Залежності** | `requirements.txt` | `numpy`, `scipy`, `fastapi`, `streamlit`, `pytest` |

> Код ядра ≈ 140 рядків, повний репо ≈ 290 рядків — ідеально для демонстрацій та швидкого прототипування.

---

## 2. Встановлення

### 2.1 Локальний запуск (Python ≥ 3.11)
```bash
git clone https://github.com/<your>/dmsir-mini.git
cd dmsir-mini
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
