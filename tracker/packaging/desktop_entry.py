import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from tracker.desktop import main
    raise SystemExit(main())
