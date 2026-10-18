from flask import Flask, render_template_string, request, redirect
import yt_dlp

app = Flask(__name__)

# واجهة الموقع البسيطة (HTML)
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>محمل فيديوهات يوتيوب سريع</title>
    <style>
        body { font-family: Arial, sans-serif; background: #f4f4f9; text-align: center; padding: 50px; }
        .container { background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); max-width: 500px; margin: auto; }
        input[type="text"] { width: 90%; padding: 10px; margin: 15px 0; border: 1px solid #ccc; border-radius: 5px; }
        button { background: #ff0000; color: white; border: none; padding: 10px 20px; border-radius: 5px; cursor: pointer; font-size: 16px; }
        button:hover { background: #cc0000; }
        .footer { margin-top: 20px; font-size: 12px; color: #777; }
    </style>
</head>
<body>
    <div class="container">
        <h2>📥 محمل فيديوهات يوتيوب</h2>
        <p>ضع رابط الفيديو بالأسفل للحصول على رابط التحميل المباشر</p>
        <form action="/download" method="POST">
            <input type="text" name="url" placeholder="الصق رابط يوتيوب هنا..." required>
            <br>
            <button type="submit">استخراج رابط التحميل</button>
        </form>
        <div class="footer">مشروع تعليمي - أداة تحميل الفيديو</div>
    </div>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

@app.route('/download', methods=['POST'])
def download():
    video_url = request.form.get('url')
    
    # إعدادات yt-dlp لاستخراج الرابط المباشر فقط دون تحميله على السيرفر
    ydl_opts = {
        'format': 'best',
        'quiet': True,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            # جلب الرابط المباشر للفيديو
            direct_download_url = info.get('url')
            
            # إعادة توجيه المستخدم للرابط المباشر ليبدأ التحميل تلقائياً على هاتفه أو جهازه
            return redirect(direct_download_url)
    except Exception as e:
        return f"حدث خطأ أثناء استخراج الرابط: {str(e)} <br><a href='/'>العودة للرئيسية</a>"

if __name__ == '__main__':
    app.run(debug=True)
