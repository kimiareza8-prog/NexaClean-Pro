# Changelog

## NexaClean Pro 3.4

- رفع خطای `takeown: File or Directory not found` برای مسیرهایی که با `/` از رابط انتخاب می‌شدند؛ مسیر ابزارهای Windows اکنون به فرمت Native مانند `D:\\Windows` نرمال می‌شود.
- بازطراحی Force Folder Delete تا `icacls /T` از همان ابتدای عملیات روی کل Windows اجرا نشود. ابتدا مجوز ریشه اصلاح و حذف مستقیم امتحان می‌شود.
- استفاده از SID داخلی Administrators برای `icacls` تا نام گروه به زبان Windows وابسته نباشد.
- جلوگیری از Deadlock خروجی `takeown/icacls` با انتقال خروجی به فایل موقت به‌جای PIPE.
- نمایش Heartbeat هر چند ثانیه هنگام عملیات طولانی، همراه با زمان سپری‌شده.
- Timeout ایمنی برای ابزارهای Windows و توقف کامل Process Tree با `taskkill /T /F`.
- جلوگیری از دنبال‌کردن Symbolic Link/Reparse Point تا حد ممکن در عملیات مالکیت و ACL.
- محدود کردن نمونه خطاهای حذف در حافظه برای جلوگیری از مصرف زیاد RAM در پوشه‌های بسیار بزرگ.

## NexaClean Pro 3.3

- نام‌گذاری و بسته‌بندی پروژه با عنوان NexaClean Pro.
- حفظ موتور اسکن زنده فایل‌های تکراری با Quick Hash و SHA-256.
- حذف گروهی فایل‌های تکراری و حذف امن با Recycle Bin در صورت نصب `send2trash`.
- Smart Photo Gallery با دسته‌بندی بر اساس نوع، رزولوشن، حجم و جهت تصویر.
- تشخیص Screenshot، UI/Icon، Cache/Temp، رزولوشن پایین، عکس شخصی احتمالی و تحلیل اختیاری کیفیت.
- نگه‌داشتن تصاویر انتخابی در حذف گروهی با گزینه «نگه دار».
- تب Force Folder Delete برای پوشه‌های قدیمی یا Access Denied با takeown/icacls/attrib و حذف بعد از Restart.
- محافظت سخت در برابر حذف Windows فعال، Program Files/ProgramData فعال، ریشه درایو و کل پروفایل کاربر فعلی.
- اضافه شدن نصب خودکار Windows با `SETUP.bat`، محیط مجازی `.venv` و فایل‌های اجرای عادی/Administrator/Debug.
