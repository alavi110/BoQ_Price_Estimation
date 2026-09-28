"""
Create sample BoQ Excel fixture for testing.
"""
import openpyxl
from openpyxl.styles import Font, Alignment
from pathlib import Path

wb = openpyxl.Workbook()
ws = wb.active
ws.title = "BoQ Items"

# Header row
headers = ["Code", "Description", "Unit", "Base Price", "Quantity"]
for col, header in enumerate(headers, 1):
    cell = ws.cell(row=1, column=col, value=header)
    cell.font = Font(bold=True)
    cell.alignment = Alignment(horizontal="center")

# Sample data - 3 chapters, ~50 items
# Chapter 101: Concrete Works
concrete_items = [
    ("101-001", "بنیان‌ریزی آهار مصرفی بتن", "متر مکعب", 2500000, 100),
    ("101-002", "اجاره و نصب سقاله فلزی", "متر مربع", 150000, 500),
    ("101-003", "آرمانه‌ریزی و بتن‌ریزی سیم‌بeton", "متر مکعب", 1800000, 200),
    ("101-004", "تهیه و نصب خمیر بتن آهار مصرفی", "متر مکعب", 3200000, 150),
    ("101-005", "کمک‌های بتن‌ریزی (ویبراتور،inio)", "ساعت", 500000, 300),
    ("101-006", "علاج و نگهداری بتن (ابکاری، پوشش)", "متر مربع", 80000, 800),
    ("101-007", "فروش و تحویل بتن آهار مصرفی اضافه", "متر مکعب", 2800000, 50),
    ("101-008", "تنبیه‌های بتن‌ریزی در ارتفاع", "متر مکعب", 450000, 100),
    ("101-009", "اجرای جداکننده‌های dilatation", "متر طول", 120000, 200),
    ("101-010", "انبارداری و نگهداری مصالح بتن", "متر مکعب", 50000, 100),
]

# Chapter 201: Steel Works
steel_items = [
    ("201-001", "تهیه و نصب میلگرد ریب A-IV", "تن", 45000000, 50),
    ("201-002", "تهیه و نصب پیچ و مهره فلزی", "عدد", 25000, 5000),
    ("201-003", "جوشکاری میلگردهای المسلحة", "متر طول", 15000, 2000),
    ("201-004", "برش و خم میلگرد به طرح", "تن", 800000, 60),
    ("201-005", "نصب اتصالات فولادی پیش‌ساخته", "عدد", 50000, 200),
    ("201-006", "سازماندهی و انبارداری مصالح فلزی", "تن", 200000, 50),
    ("201-007", "کنترل کیفیت جوشکاری (radiography)", "عدد", 500000, 50),
    ("201-008", "پیش‌سازی میلگرد در کارگاه", "تن", 1200000, 30),
    ("201-009", "پوشش محافظتی میلگرد (epoxy)", "متر مربع", 80000, 500),
    ("201-010", "تحویل و تست مصالح فلزی", "تن", 100000, 50),
]

# Chapter 301: Architectural Finishes
finish_items = [
    ("301-001", "اجرای کاشی و سرامیک کف و دیوار", "متر مربع", 450000, 1000),
    ("301-002", "اجرای سنگ تراورتن و مرمر", "متر مربع", 850000, 300),
    ("301-003", "رنگ‌آمیزی ساختمان ( Plastic )", "متر مربع", 120000, 2000),
    ("301-004", "نصب سقف کاذب (Drywall)", "متر مربع", 350000, 1500),
    ("301-005", "اجرای کف پاركت و لمینت", "متر مربع", 400000, 800),
    ("301-006", "نصب درب و پنجره UPVC", "متر مربع", 1200000, 400),
    ("301-007", "عایق‌بندی رطوبتی و حرارتی", "متر مربع", 180000, 1200),
    ("301-008", "اجرای فایلون و مزایین", "متر مربع", 250000, 500),
    ("301-009", "نصب تجهیزات بهداشتی", "عدد", 800000, 50),
    ("301-010", "تمیزکاری و تحویل نهایی", "متر مربع", 15000, 3000),
]

# Chapter 401: Mechanical Works
mech_items = [
    ("401-001", "نصب سیستم تهویه و ventilation", "متر مکعب", 350000, 5000),
    ("401-002", "لوله‌کشی آب و فاضلاب", "متر طول", 180000, 2000),
    ("401-003", "نصب پمپ و موتور آب", "عدد", 15000000, 10),
    ("401-004", "سیستم اطفای حریق", "متر مربع", 250000, 3000),
]

# Chapter 501: Electrical Works
elec_items = [
    ("501-001", "لوله‌کشی و سیم‌کشی برق", "متر طول", 80000, 5000),
    ("501-002", "نصب تابلو و برکر", "عدد", 2500000, 20),
    ("501-003", "سیستم نور اضطراری", "متر مربع", 120000, 3000),
    ("501-004", "نصب تجهیزات اتوماسیون", "عدد", 5000000, 30),
]

all_items = concrete_items + steel_items + finish_items + mech_items + elec_items

for row_idx, (code, desc, unit, price, qty) in enumerate(all_items, 2):
    ws.cell(row=row_idx, column=1, value=code)
    ws.cell(row=row_idx, column=2, value=desc)
    ws.cell(row=row_idx, column=3, value=unit)
    ws.cell(row=row_idx, column=4, value=price)
    ws.cell(row=row_idx, column=5, value=qty)

# Set column widths
ws.column_dimensions['A'].width = 15
ws.column_dimensions['B'].width = 50
ws.column_dimensions['C'].width = 15
ws.column_dimensions['D'].width = 18
ws.column_dimensions['E'].width = 12

# Save
output_path = Path(__file__).parent / "sample_boq.xlsx"
output_path.parent.mkdir(parents=True, exist_ok=True)
wb.save(output_path)
print(f"Created {output_path} with {len(all_items)} items")