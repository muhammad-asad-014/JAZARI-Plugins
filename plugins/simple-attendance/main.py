"""
Simple Attendance Plugin
Works for both Corporate and Education environments
"""

class Plugin:
    def __init__(self):
        self.name = "AERO"
        self.version = "1.0.0"
        self.description = "Automated Enrollment and Record Organizer. Web-based attendance tracking"
    
    def setup(self, app):
        """Called when plugin is installed"""
        print(f"[Plugin] {self.name} v{self.version} installed successfully")
        return True
    
    def get_routes(self):
        """Return routes this plugin handles"""
        return [
            {"url": "/attendance", "methods": ["GET"], "handler": "attendance_page"},
            {"url": "/attendance/mark", "methods": ["POST"], "handler": "mark_attendance"},
            {"url": "/attendance/report", "methods": ["GET"], "handler": "attendance_report"}
        ]
    
    def get_menu_items(self, user_role):
        """Return menu items based on user role"""
        if user_role in ["admin", "employee", "teacher"]:
            return [{
                "name": "Attendance",
                "icon": "fa-clock",
                "url": "/attendance",
                "order": 3
            }]
        return []
    
    def get_widgets(self, user_role):
        """Return dashboard widgets"""
        return [{
            "name": "Today's Attendance",
            "template": "simple-attendance/widget.html",
            "size": "col-md-6"
        }]

plugin = Plugin()
