Movie Library.app test launcher bundle for v28.5.8. It starts the existing local Flask server only if port 8765 is not already responding, then opens the local library.


v28.5.9: launcher logs now write to the app folder logs/ directory. This remains a test bundle and should stay beside the server files for now.


v28.5.10: fixed the bundle path calculation so double-clicking the test .app resolves the server folder beside the bundle instead of looking one folder too high.
