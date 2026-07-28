
from app.config import BASE_DIR
from app.services.input_loader import load_package_input
from app.services.package_builder import PackageBuilder


def main() -> None:
    json_path = (BASE_DIR / "data" / "input" / "package_input.json").resolve()

    payload = load_package_input(json_path)

    print("=== Входные данные ===")
    print(f"ФИО: {payload.fio}")
    print(f"Индекс: {payload.employee_index}")
    print(f"Документов: {len(payload.documents)}")
    if payload.iin:
        print(f"ИИН: {payload.iin}")
    if payload.birth_date:
        print(f"Дата рождения: {payload.birth_date}")
    if payload.company:
        print(f"Организация: {payload.company}")
    if payload.profession:
        print(f"Профессия: {payload.profession}")

    builder = PackageBuilder()
    result = builder.build(payload)

    print("Пакет успешно собран.")
    print(f"Папка: {result.build_dir}")
    print(f"ZIP:   {result.zip_path}")


if __name__ == "__main__":
    main()
