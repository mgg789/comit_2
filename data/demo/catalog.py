"""Deterministic fictional people and companies; never fabricate verified evidence."""
from uuid import UUID, uuid5

VERSION = "comit-demo-2026-v1"
NAMESPACE = UUID("80ee3854-5836-41da-947f-4482c08877b9")
CITIES = ["Москва", "Санкт-Петербург", "Казань", "Екатеринбург", "Новосибирск", "Нижний Новгород", "Самара", "Томск", "Пермь", "Воронеж"]
PEOPLE = [("Анна", "Соколова"), ("Михаил", "Орлов"), ("Елена", "Морозова"), ("Дмитрий", "Белов"), ("Мария", "Кузнецова"), ("Артём", "Волков"), ("Софья", "Смирнова"), ("Алексей", "Егоров"), ("Дарья", "Лебедева"), ("Илья", "Тихонов"), ("Полина", "Зайцева"), ("Никита", "Фролов"), ("Вера", "Макарова"), ("Роман", "Крылов"), ("Ольга", "Громова"), ("Павел", "Романов"), ("Ксения", "Тарасова"), ("Сергей", "Зорин"), ("Алиса", "Никитина"), ("Тимофей", "Лазарев"), ("Юлия", "Захарова"), ("Иван", "Медведев"), ("Екатерина", "Савельева"), ("Денис", "Гусев"), ("Надежда", "Сергеева"), ("Максим", "Осипов"), ("Анастасия", "Павлова"), ("Глеб", "Чернов"), ("Валерия", "Виноградова"), ("Андрей", "Фомин")]
COMPANIES = ["Листва Лаб", "Контур Ритма", "Северная Сота", "Маяк Потока", "Клевер Данных", "Вектор Утра", "Облако Маршрутов", "Ясная Среда", "Тихий Спектр", "Линия Решений", "Модуль Города", "Речной Код", "Пульс Команды", "Искра Систем", "Атлас Сигналов", "Точка Баланса"]
PRODUCTS = ["платформа записи в небольшие клиники", "сервис управления складом локальных магазинов", "личный кабинет онлайн-школы", "планировщик доставки фермерских продуктов", "система бронирования коворкингов", "панель мониторинга производственных датчиков", "сервис согласования документов", "каталог культурных мероприятий", "инструмент учёта расходов команды", "платформа поддержки благотворительных проектов", "сервис управления подписками", "кабинет городского велопроката"]
TRACKS = {
 "backend": ("Backend-разработчик", ["python", "go", "node", "postgres", "docker"], ["rest_api", "sql", "architecture_thinking"], "проектирование API, фоновые задания и надёжная работа с данными", "сокращение времени обработки запросов и безопасные миграции базы", "контрактные тесты и наблюдаемость сервисов"),
 "frontend": ("Frontend-разработчик", ["react", "vue", "tailwind", "html_css_jquery"], ["ux_userflow", "rest_api", "architecture_thinking"], "доступные интерфейсы, формы и состояния загрузки", "ускорение открытия личного кабинета и сокращение ошибок в формах", "компонентная библиотека и браузерные тесты"),
 "fullstack": ("Fullstack-разработчик", ["node", "react", "postgres", "docker"], ["rest_api", "sql", "ux_userflow"], "пользовательские сценарии от интерфейса до базы данных", "запуск нового кабинета и устранение повторных запросов", "контракты API и согласованность клиентских данных"),
 "mobile": ("Разработчик мобильных приложений", ["react", "sqlite", "figma"], ["ux_userflow", "rest_api", "lowlevel_optimization"], "мобильная навигация, офлайн-режим и синхронизация", "стабильная синхронизация при слабой сети и быстрый старт приложения", "React Native, профилирование и проверка на реальных устройствах"),
 "devops": ("DevOps-инженер", ["docker", "python", "postgres"], ["devops", "cybersecurity", "architecture_thinking"], "автоматизация поставки, мониторинг и восстановление сервисов", "сокращение ручных операций и проверяемое восстановление из резервных копий", "Linux, CI/CD и разбор эксплуатационных инцидентов"),
 "data": ("Инженер данных", ["python", "postgres", "mysql"], ["sql", "data_analytics_stats", "math"], "загрузка данных, проверки качества и аналитические витрины", "ускорение обновления отчётности и контроль пропущенных событий", "идемпотентные загрузки и воспроизводимые SQL-запросы"),
 "ml": ("ML-инженер", ["python", "postgres", "docker"], ["classical_ml", "data_analytics_stats", "nlp_and_arbitrary_data"], "подготовка выборок, сравнение моделей и оценка качества", "воспроизводимая оценка на отложенной выборке и контроль утечек данных", "анализ ошибок моделей и мониторинг качества после запуска"),
 "qa": ("Инженер по тестированию", ["python", "node", "postgres"], ["rest_api", "sql", "ux_userflow"], "риск-ориентированные сценарии, API и проверка пользовательских путей", "сокращение нестабильных тестов и понятные отчёты о дефектах", "автоматизация API/UI и исследовательское тестирование"),
}


def stable_id(key: str) -> str:
    """Stable identities make retries and independent exports refer to the same fictional record."""
    return str(uuid5(NAMESPACE, key))


def build_catalog() -> dict:
    """Return 240 varied declarations, 16 fictional companies and 48 salary-bearing vacancies."""
    candidates, employers, vacancies = [], [], []
    grades = ["junior", "middle", "senior"]
    for index, company in enumerate(COMPANIES):
        key = f"employer-{index:02}"
        employers.append({"key": key, "id": stable_id(key), "email": f"{key}@comit-demo.invalid", "company_name": company + " · демо"})
    for direction_index, (direction, (role, stacks, domains, responsibilities, outcome, practices)) in enumerate(TRACKS.items()):
        for index, (first, surname) in enumerate(PEOPLE):
            key = f"candidate-{direction}-{index:02}"
            grade = grades[index % 3]
            level = {"junior": 2, "middle": 4, "senior": 6}[grade]
            years = {"junior": 1, "middle": 3, "senior": 7}[grade] + index % 2
            city = CITIES[(index + direction_index) % len(CITIES)]
            company = COMPANIES[(index + direction_index * 2) % len(COMPANIES)]
            product = PRODUCTS[(index * 3 + direction_index + index // 4) % len(PRODUCTS)]
            low = {"junior": 70000, "middle": 140000, "senior": 230000}[grade] + (index % 5) * 5000
            name = first + " " + PEOPLE[(index + direction_index * 2) % len(PEOPLE)][1]
            fields = {"display_name": name, "city": city, "salary": {"from_rub": low, "to_rub": low + 40000 + index % 3 * 10000},
                "primary_direction": direction, "declared_grade": grade,
                "work_types": [["remote"], ["hybrid", "remote"], ["onsite", "hybrid"]][index % 3],
                "relocation_ready": index % 5 == 0, "relocation_cities": ["Москва", "Казань"] if index % 5 == 0 else [],
                "domains": [{"key": value, "level": min(7, level + (position + index) % 2)} for position, value in enumerate(domains)],
                "stacks": [{"key": value, "level": min(7, level + (position + index) % 2)} for position, value in enumerate(stacks) if position < 3 or index % 2 == 0],
                "soft_skills": [{"key": "teamwork", "level": min(7, level + 1)}, {"key": "self_organization", "level": level}],
                "description": f"Демонстрационное резюме вымышленного специалиста. {role}, {years} года практики. Моя основная область — {responsibilities}. Последний продукт — {product}. В работе важны {practices}. Ищу команду с понятными целями, регулярным обсуждением решений и вниманием к качеству.",
                "experience": [{"title": f"{role} в продуктовой команде", "organization": company + " (вымышленная компания)", "description": f"{2026-years}–2026. Продукт: {product}. Зона ответственности: {responsibilities}. Результат команды: {outcome}. Участвую в планировании, ревью и сопровождении изменений.", "facts": {"duration_months": years * 12, "role": "qa" if direction == "qa" else "devops" if direction == "devops" else "developer", "production_support": grade != "junior", "stacks": stacks[:3], "domains": domains[:2]}}],
                "projects": [{"title": product.capitalize(), "description": f"Учебный демонстрационный проект: {responsibilities}. Использованы {', '.join(stacks[:3])}. Отдельно проработаны {practices}. Все сведения вымышлены."}],
                "achievements": [], "preferences": ["Ревью и обмен знаниями", ["Гибкое начало рабочего дня", "Небольшая продуктовая команда", "Сложные технические задачи", "Понятное наставничество"][index % 4]]}
            candidates.append({"key": key, "id": stable_id(key), "email": f"{key}@comit-demo.invalid", "fields": fields})
        for index in range(6):
            key = f"vacancy-{direction}-{index:02}"
            employer = employers[(direction_index * 2 + index) % len(employers)]
            grade = grades[index % 3]
            low = {"junior": 65000, "middle": 135000, "senior": 225000}[grade]
            product = PRODUCTS[(direction_index + index) % len(PRODUCTS)]
            fields = {"title": f"{role} {grade.capitalize()}", "class": direction, "zp_min": low, "zp_max": low + 80000,
                "daily": "Полная занятость, 5/2; гибкое начало дня", "city": CITIES[(direction_index + index) % len(CITIES)],
                "type": ["remote", "hybrid", "onsite"][index % 3],
                "bio": f"Демонстрационная вакансия вымышленной компании. Мы развиваем продукт: {product}. Ищем коллегу в команду из {4+index} инженеров. Задачи: {responsibilities}. В первые месяцы вместе проработаем {outcome}. Ожидаем практический опыт с {', '.join(stacks[:3])}, умение обсуждать ограничения и проверять результат. В команде приняты {practices}. Предлагаем прозрачную зарплатную вилку, наставника на старте, технику и время на обучение. Процесс знакомства: короткий разговор о задачах, техническое обсуждение и обратная связь.",
                "domens": {key: {"junior": 2, "middle": 4, "senior": 5}[grade] for key in domains[:2]},
                "stacks": {key: {"junior": 2, "middle": 4, "senior": 5}[grade] for key in stacks[:3]},
                "soft_skills": {"teamwork": 3, "self_organization": 3},
                "metadata": {"demo_catalog": VERSION, "demo_key": key, "company_name": employer["company_name"], "grade": grade, "synthetic": True}}
            vacancies.append({"key": key, "owner_id": employer["id"], "fields": fields})
    return {"version": VERSION, "synthetic": True, "candidates": candidates, "employers": employers, "vacancies": vacancies}
