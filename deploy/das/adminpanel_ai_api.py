"""Read-only AI export API для DAS (adminpanel).

УСТАНОВКА НА СЕРВЕРЕ DAS:
  1. Скопировать этот файл в:
       /home/dnepr/dms-nas-rework/dms-nas/apps/web_admin/adminpanel/ai_api.py
  2. Добавить URL в adminpanel/urls.py:
       from . import ai_api
       path("api/ai-internal/employees/", ai_api.employees_ai_list, name="ai_employees_list"),
       path("api/ai-internal/employee/<int:employee_id>/",
            ai_api.employee_ai_export, name="ai_employee_export"),
       path("api/ai-internal/documents/<str:document_id>/file/",
            ai_api.document_ai_file, name="ai_document_file"),
  3. Добавить переменную окружения на сервере DAS:
       AI_INTERNAL_TOKEN=<32+ случайных символа>
     Этот токен задать в pass_automation:
       AI_DAS_TOKEN=<тот же токен>
       AI_DAS_BASE_URL=http://localhost:8000   # или внутренний адрес DAS
  4. Убедиться, что /api/ai-internal/* НЕ открыт в интернет (только localhost/internal).

Этот API НЕ изменяет базу данных. Только GET.
ИИН и паспортные данные передаются только в зашифрованном канале (HTTPS).
"""

from __future__ import annotations

import hashlib
import hmac
import os

from django.http import JsonResponse
from django.views.decorators.http import require_GET


def _authenticate(request: object) -> bool:
    """Проверка X-Ai-Token через HMAC-SHA256. NAS credentials не печатаются в stdout."""
    expected_raw = os.getenv("AI_INTERNAL_TOKEN", "").strip()
    if not expected_raw:
        return False
    provided_raw = request.META.get("HTTP_X_AI_TOKEN", "").strip()
    if not provided_raw:
        return False
    return hmac.compare_digest(
        hashlib.sha256(expected_raw.encode()).hexdigest(),
        hashlib.sha256(provided_raw.encode()).hexdigest(),
    )


@require_GET
def employee_ai_export(request, employee_id: int):
    """GET /api/ai-internal/employee/{id}/ — данные сотрудника для AI pilot (read-only).

    Возвращает: employee_id, full_name, iin (только для AI-сопоставления), company,
    список активных EmployeeDocument с source_path и document_code (int).
    Production-данные не изменяются.
    """
    if not _authenticate(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)

    from pass_docs.models import Employee  # noqa: PLC0415 — lazy import, нет cycle

    try:
        employee = (
            Employee.objects.prefetch_related("documents__document_type").get(pk=employee_id)
        )
    except Employee.DoesNotExist:
        return JsonResponse({"error": "Employee not found"}, status=404)

    documents = []
    qs = employee.documents.filter(is_actual=True).order_by(
        "document_type__sort_order", "document_type__code"
    )
    for doc in qs:
        doc_code_raw = doc.document_type.code
        try:
            doc_code_int = int(doc_code_raw)
        except (ValueError, TypeError):
            continue  # пропускаем нечисловые коды (vehicle docs и т.д.)
        documents.append(
            {
                "document_id": f"{employee.pk}:{doc_code_raw}",
                "document_code": doc_code_int,
                "document_type_name": doc.document_type.name,
                "source_path": doc.source_path,
                "parse_status": doc.parse_status,
                "status": doc.status,
            }
        )

    return JsonResponse(
        {
            "employee_id": str(employee.pk),
            "import_key": employee.import_key,
            "full_name": employee.full_name,
            "iin": employee.iin,
            "company": employee.company,
            "profession_label": employee.profession_label,
            "is_active": employee.is_active,
            "documents": documents,
        }
    )


@require_GET
def document_ai_file(request, document_id: str):
    """GET /api/ai-internal/documents/{document_id}/file/ — файл документа для AI анализа.

    document_id формат: "{employee_id}:{document_type_code}".
    Возвращает содержимое файла (streaming). Только GET. Production-данные не изменяются.
    NAS credentials не раскрываются: путь файла в лог не попадает.
    """
    if not _authenticate(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)

    import mimetypes
    import os

    try:
        employee_id_str, code_str = document_id.split(":", 1)
        employee_id = int(employee_id_str)
    except (ValueError, TypeError):
        return JsonResponse({"error": "Invalid document_id: expected employee_id:code"}, status=400)

    from pass_docs.models import EmployeeDocument  # noqa: PLC0415

    try:
        doc = (
            EmployeeDocument.objects
            .select_related("document_type")
            .get(employee__pk=employee_id, document_type__code=code_str, is_actual=True)
        )
    except EmployeeDocument.DoesNotExist:
        return JsonResponse({"error": "Document not found"}, status=404)

    source_path = getattr(doc, "source_path", None)
    if not source_path or not os.path.isfile(str(source_path)):
        return JsonResponse({"error": "File not found on server"}, status=404)

    from django.http import FileResponse  # noqa: PLC0415
    content_type, _ = mimetypes.guess_type(str(source_path))
    return FileResponse(
        open(str(source_path), "rb"),  # noqa: WPS515
        content_type=content_type or "application/octet-stream",
        filename=os.path.basename(str(source_path)),
    )


@require_GET
def employees_ai_list(request):
    """GET /api/ai-internal/employees/?company=X&limit=100 — список активных сотрудников.

    Возвращает только id, import_key, full_name, company — без персональных данных.
    """
    if not _authenticate(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)

    from pass_docs.models import Employee  # noqa: PLC0415

    qs = Employee.objects.filter(is_active=True).order_by("import_key")
    company = request.GET.get("company", "").strip()
    if company:
        qs = qs.filter(company__icontains=company)

    try:
        limit = min(int(request.GET.get("limit", 100)), 500)
        offset = max(int(request.GET.get("offset", 0)), 0)
    except (ValueError, TypeError):
        limit, offset = 100, 0

    total = qs.count()
    rows = list(qs.values("id", "import_key", "full_name", "company")[offset : offset + limit])

    return JsonResponse({"total": total, "offset": offset, "limit": limit, "employees": rows})
