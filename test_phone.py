import cv2

print("Starting camera...")

camera = cv2.VideoCapture(0, cv2.CAP_DSHOW)

if not camera.isOpened():
    print("ERROR: Camera could not be opened")
    input("Press Enter to exit...")
    exit()

print("Camera opened successfully!")

while True:

    success, frame = camera.read()

    if not success:
        print("ERROR: Could not read camera frame")
        break

    cv2.imshow("Camera Test", frame)

    key = cv2.waitKey(1) & 0xFF

    if key == ord("q"):
        break

camera.release()
cv2.destroyAllWindows()

print("Camera test finished.")
input("Press Enter to close...")