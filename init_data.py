"""Скрипт для инициализации тестовых данных"""
from models.database import init_db, get_db_session
from models.shop import Shop
from models.category import Category

def init_test_data():
    """Создание тестовых данных"""
    init_db()
    db = next(get_db_session())
    
    try:
        # Проверяем, есть ли уже данные
        if db.query(Shop).count() > 0:
            print("Магазины уже существуют")
            return
        
        # Создаем магазины
        shops = [
            Shop(name="Магазин №1"),
            Shop(name="Магазин №2"),
            Shop(name="Магазин №3"),
        ]
        for shop in shops:
            db.add(shop)
        
        # Создаем категории
        categories = [
            Category(name="Касса и оплата", description="Проблемы с кассой или оплатой", sla_hours=2),
            Category(name="Техника", description="Проблемы с оборудованием", sla_hours=24),
            Category(name="Интернет", description="Проблемы с интернетом", sla_hours=12),
            Category(name="Другое", description="Прочие проблемы", sla_hours=24),
        ]
        for category in categories:
            db.add(category)
        
        db.commit()
        print("Тестовые данные созданы:")
        print(f"- Магазинов: {len(shops)}")
        print(f"- Категорий: {len(categories)}")
        
    finally:
        db.close()

if __name__ == '__main__':
    init_test_data()
