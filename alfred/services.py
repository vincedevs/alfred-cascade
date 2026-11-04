"""Alfred's long-lived parts, built once at startup (Services.start) and shared by every
conversation: settings, the database, background jobs, their questions, memory,
reminders (with timers and alarms), Google Chat, shared web pages, and the ringer that
sounds timers and alarms."""

from dataclasses import dataclass

from loguru import logger

from alfred.alarm_backup import AlarmBackup
from alfred.audit import AuditLog
from alfred.chat import Chat
from alfred.config import PROJECT_ROOT, Config
from alfred.db import Database
from alfred.memory import Memory
from alfred.questions import Questions
from alfred.reminders import Reminders
from alfred.ringer import Ringer
from alfred.tasks import TaskRunner
from alfred.web import WebShare, job_text, start_web


@dataclass
class Services:
    config: Config
    db: Database
    tasks: TaskRunner
    questions: Questions
    memory: Memory
    reminders: Reminders
    chat: Chat
    ringer: Ringer | None = None  # set by __main__.py, which owns the speaker
    web: WebShare | None = None  # jobs' web pages on the home network (web.py)

    @classmethod
    def start(cls, config: Config, on_job_finished) -> "Services":
        """Open the database, resume unfinished jobs and catch up on missed reminders."""
        config.workspace_dir.mkdir(parents=True, exist_ok=True)
        db = Database(config.database_path)
        questions = Questions(db)
        memory = Memory(db, config)
        audit = AuditLog(config.audit_path)
        web = start_web(config.workspace_dir, config.web_port)
        sharing = job_text(web.port) if web else ""
        tasks = TaskRunner(
            db,
            config,
            questions,
            audit,
            on_finished=on_job_finished,
            # jobs know where you are, what Alfred knows about you, and where pages go
            about_user=lambda: config.home_text() + memory.profile_text() + sharing,
        )
        if resumed := tasks.resume_unfinished():
            logger.info(f"Resumed {resumed} unfinished job(s)")
        # the computer rings an alarm itself if Alfred isn't running (alarm_backup.py)
        backup = AlarmBackup(PROJECT_ROOT / "sounds" / "alarm.wav")
        reminders = Reminders(db, config, tasks, backup=backup if backup.available else None)
        reminders.catch_up()
        chat = Chat(config.google_chat_spaces, questions, audit)
        return cls(config, db, tasks, questions, memory, reminders, chat, web=web)

    def has_news(self) -> bool:
        """Is there anything Alfred should speak up about?"""
        return bool(self.db.untold_results() or self.questions.waiting() or self.reminders.unsaid())
