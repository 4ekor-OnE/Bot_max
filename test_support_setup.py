"""Скрипт для настройки тестового пользователя с ролью support"""
from models.database import init_db, get_db_session
from models.user import User, UserRole
from models.ticket import Ticket, TicketStatus, TicketPriority
from models.shop import Shop
from models.category import Category
from datetime import datetime, timedelta, timezone

def setup_test_support():
    """Создание тестового пользователя support и тестовых заявок"""
    init_db()
    db = next(get_db_session())
    
    try:
        # Запрашиваем max_id у пользователя
        print("=" * 50)
        print("НАСТРОЙКА ТЕСТОВОГО СПЕЦИАЛИСТА ТП")
        print("=" * 50)
        print("\nЧтобы узнать свой max_id:")
        print("1. Откройте бота в MAX мессенджере")
        print("2. Отправьте команду /start")
        print("3. В логах бота будет виден ваш max_id")
        print("   (проверьте: tail -f /tmp/bot.log)")
        print("\nИли используйте свой MAX ID из мессенджера")
        print("=" * 50)
        
        max_id = input("\nВведите ваш max_id (или нажмите Enter для использования тестового): ").strip()
        
        if not max_id:
            max_id = "999999999"  # Тестовый ID
            print(f"Используется тестовый max_id: {max_id}")
        
        # Проверяем, существует ли пользователь
        user = db.query(User).filter(User.max_id == max_id).first()
        
        if user:
            # Обновляем роль существующего пользователя
            user.role = UserRole.SUPPORT
            print(f"\n✅ Роль пользователя {max_id} изменена на SUPPORT")
        else:
            # Создаем нового пользователя с ролью support
            user = User(
                max_id=max_id,
                username="test_support",
                first_name="Тестовый Специалист",
                role=UserRole.SUPPORT
            )
            db.add(user)
            print(f"\n✅ Создан новый пользователь с ролью SUPPORT: {max_id}")
        
        db.commit()
        db.refresh(user)
        
        # Создаем тестовые заявки, если их нет
        existing_tickets = db.query(Ticket).count()
        
        if existing_tickets == 0:
            print("\n📋 Создание тестовых заявок...")
            
            # Получаем магазины и категории
            shops = db.query(Shop).all()
            categories = db.query(Category).all()
            
            if not shops or not categories:
                print("⚠️  Сначала запустите init_data.py для создания магазинов и категорий")
                return
            
            # Создаем обычного пользователя для заявок
            test_user = db.query(User).filter(User.role == UserRole.USER).first()
            if not test_user:
                test_user = User(
                    max_id="111111111",
                    username="test_user",
                    first_name="Тестовый Пользователь",
                    role=UserRole.USER
                )
                db.add(test_user)
                db.commit()
                db.refresh(test_user)
            
            # Создаем несколько тестовых заявок
            test_tickets = [
                Ticket(
                    user_id=test_user.id,
                    shop_id=shops[0].id,
                    category_id=categories[0].id,
                    title="Не работает касса",
                    description="Касса не принимает карты, только наличные",
                    status=TicketStatus.NEW,
                    priority=TicketPriority.HIGH,
                    sla_deadline=datetime.now(timezone.utc) + timedelta(hours=2)
                ),
                Ticket(
                    user_id=test_user.id,
                    shop_id=shops[1].id,
                    category_id=categories[1].id,
                    title="Сломался принтер",
                    description="Принтер не печатает чеки",
                    status=TicketStatus.NEW,
                    priority=TicketPriority.NORMAL,
                    sla_deadline=datetime.now(timezone.utc) + timedelta(hours=24)
                ),
                Ticket(
                    user_id=test_user.id,
                    shop_id=shops[0].id,
                    category_id=categories[2].id,
                    title="Проблемы с интернетом",
                    description="Интернет работает очень медленно",
                    status=TicketStatus.NEW,
                    priority=TicketPriority.URGENT,
                    sla_deadline=datetime.now(timezone.utc) + timedelta(hours=12)
                ),
            ]
            
            for ticket in test_tickets:
                db.add(ticket)
            
            db.commit()
            print(f"✅ Создано {len(test_tickets)} тестовых заявок")
        
        print("\n" + "=" * 50)
        print("✅ НАСТРОЙКА ЗАВЕРШЕНА")
        print("=" * 50)
        print(f"\nВаш max_id: {max_id}")
        print(f"Роль: SUPPORT")
        print("\nТеперь:")
        print("1. Откройте бота в MAX мессенджере")
        print("2. Отправьте /start")
        print("3. Вы увидите меню специалиста ТП")
        print("\nДоступные функции:")
        print("  • 🆕 Новые заявки")
        print("  • ⚙️ В работе")
        print("  • 🏪 По магазинам")
        print("  • 📊 Моя статистика")
        print("=" * 50)
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
    finally:
        db.close()

if __name__ == '__main__':
    setup_test_support()
