"""兼容旧命令；新部署请使用 scripts/create_staff.py。"""

try:
    from scripts.create_staff import main
except ModuleNotFoundError:  # Compatible with ``python scripts/create_doctor.py``.
    from create_staff import main


if __name__ == "__main__":
    main()
