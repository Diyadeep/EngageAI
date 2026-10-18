import os
import cv2
import numpy as np
import urllib.request
import mediapipe as mp
from ultralytics import YOLO

# ---------------------------------------------------------
# Path Configurations & Model Lazy Loading
# ---------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
yolo_path = os.path.join(BASE_DIR, "yolov8n.pt")
pose_path = os.path.join(BASE_DIR, "yolov8n-pose.pt")
task_path = os.path.join(BASE_DIR, "face_landmarker.task")
haar_path = os.path.join(BASE_DIR, "haarcascade_frontalface_default.xml")

# Auto-download models if missing
if not os.path.exists(task_path):
    print("[AI INITIALIZATION] Downloading face_landmarker.task...")
    try:
        url = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
        urllib.request.urlretrieve(url, task_path)
    except Exception as e:
        print("[AI INITIALIZATION ERROR] Could not download face_landmarker.task:", e)

if not os.path.exists(haar_path):
    print("[AI INITIALIZATION] Downloading haarcascade_frontalface_default.xml...")
    try:
        url = "https://raw.githubusercontent.com/opencv/opencv/master/data/haarcascades/haarcascade_frontalface_default.xml"
        urllib.request.urlretrieve(url, haar_path)
    except Exception as e:
        print("[AI INITIALIZATION ERROR] Could not download haarcascade XML:", e)

# Load YOLO models
model = YOLO(yolo_path if os.path.exists(yolo_path) else "yolov8n.pt")
pose_model = YOLO(pose_path if os.path.exists(pose_path) else "yolov8n-pose.pt")
haar_cascade = cv2.CascadeClassifier(haar_path) if os.path.exists(haar_path) else None

# Load MediaPipe FaceLandmarker
landmarker = None
if os.path.exists(task_path):
    try:
        BaseOptions = mp.tasks.BaseOptions
        FaceLandmarker = mp.tasks.vision.FaceLandmarker
        FaceLandmarkerOptions = mp.tasks.vision.FaceLandmarkerOptions
        VisionRunningMode = mp.tasks.vision.RunningMode

        options = FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=task_path),
            running_mode=VisionRunningMode.IMAGE,
            num_faces=1,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=True
        )
        landmarker = FaceLandmarker.create_from_options(options)
        print("[AI INITIALIZATION] MediaPipe FaceLandmarker loaded successfully!")
    except Exception as e:
        print("[AI INITIALIZATION ERROR] Failed to load MediaPipe FaceLandmarker:", e)

# ---------------------------------------------------------
# Global Tracking State for Temporal Hysteresis & Smoothing
# ---------------------------------------------------------
GLOBAL_STATE = {
    "face_detected_counter": 0,
    "face_not_detected_counter": 0,
    "straight_counter": 0,
    "left_turn_counter": 0,
    "right_turn_counter": 0,
    "away_counter": 0,
    "eyes_closed_counter": 0,
    "drowsy_counter": 0,
    "phone_detected_counter": 0,
    "phone_not_detected_counter": 0,

    "current_face_status": "FACE DETECTED",
    "current_attention_status": "LOOKING STRAIGHT",
    "current_eye_status": "EYES ON SCREEN",
    "current_drowsiness_status": "ALERT",
    "current_phone_status": "NO PHONE DETECTED",
    "current_engagement_score": 100.0,
    "current_engagement_level": "HIGH"
}

def detect_phone(frame, state=None):
    if state is None:
        state = GLOBAL_STATE

    # Ensure all required state keys are present
    state.setdefault("face_detected_counter", 0)
    state.setdefault("face_not_detected_counter", 0)
    state.setdefault("straight_counter", 0)
    state.setdefault("left_turn_counter", 0)
    state.setdefault("right_turn_counter", 0)
    state.setdefault("away_counter", 0)
    state.setdefault("eyes_closed_counter", 0)
    state.setdefault("drowsy_counter", 0)
    state.setdefault("phone_detected_counter", 0)
    state.setdefault("phone_not_detected_counter", 0)

    state.setdefault("current_face_status", "FACE DETECTED")
    state.setdefault("current_attention_status", "LOOKING STRAIGHT")
    state.setdefault("current_eye_status", "EYES ON SCREEN")
    state.setdefault("current_drowsiness_status", "ALERT")
    state.setdefault("current_phone_status", "NO PHONE DETECTED")
    state.setdefault("current_engagement_score", 100.0)
    state.setdefault("current_engagement_level", "HIGH")

    h_frame, w_frame = frame.shape[:2]

    # ---------------------------------------------------------
    # 1. PHONE DETECTION (YOLO Object Detector - Class 67 cell phone)
    # ---------------------------------------------------------
    yolo_results = model(frame, verbose=False, conf=0.25)
    raw_phone_detected = False
    person_box_det = None
    max_person_area = 0

    for result in yolo_results:
        if result.boxes is None:
            continue
        for box in result.boxes:
            class_id = int(box.cls[0])
            class_name = model.names.get(class_id, "")

            # Phone detection (cell phone class)
            if class_name == "cell phone" or class_id == 67:
                raw_phone_detected = True
                px1, py1, px2, py2 = map(int, box.xyxy[0])
                cv2.rectangle(frame, (px1, py1), (px2, py2), (0, 0, 255), 3)
                cv2.putText(
                    frame,
                    "PHONE DETECTED",
                    (px1, max(py1 - 10, 25)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (0, 0, 255),
                    2
                )

            # Person detection for fallback region estimation
            elif class_name == "person" or class_id == 0:
                px1, py1, px2, py2 = map(int, box.xyxy[0])
                area = (px2 - px1) * (py2 - py1)
                if area > max_person_area:
                    max_person_area = area
                    person_box_det = (px1, py1, px2, py2)

    # Temporal smoothing for phone status
    if raw_phone_detected:
        state["phone_detected_counter"] += 1
        state["phone_not_detected_counter"] = 0
        if state["phone_detected_counter"] >= 1:
            phone_status = "PHONE DETECTED"
        else:
            phone_status = state.get("current_phone_status", "NO PHONE DETECTED")
    else:
        state["phone_not_detected_counter"] += 1
        state["phone_detected_counter"] = 0
        if state["phone_not_detected_counter"] >= 2:
            phone_status = "NO PHONE DETECTED"
        else:
            phone_status = state.get("current_phone_status", "NO PHONE DETECTED")

    phone_detected_flag = (phone_status == "PHONE DETECTED")

    # ---------------------------------------------------------
    # 2. MULTI-TIER FACE & LANDMARK DETECTION
    # Tier 1: MediaPipe FaceLandmarker (478 3D landmarks)
    # Tier 2: YOLO Pose Keypoints (0=Nose, 1=L Eye, 2=R Eye, 3=L Ear, 4=R Ear)
    # Tier 3: OpenCV Haar Cascade Frontal Face
    # Tier 4: YOLO Person / Bounding box
    # ---------------------------------------------------------
    mp_face_found = False
    landmarks_px = []
    hx1, hy1, hx2, hy2 = 0, 0, 0, 0

    if landmarker is not None:
        try:
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
            landmarker_result = landmarker.detect(mp_img)

            if landmarker_result.face_landmarks and len(landmarker_result.face_landmarks) > 0:
                mp_face_found = True
                face_lms = landmarker_result.face_landmarks[0]
                landmarks_px = [(int(lm.x * w_frame), int(lm.y * h_frame)) for lm in face_lms]

                # Face bounding box from landmarks
                xs = [pt[0] for pt in landmarks_px]
                ys = [pt[1] for pt in landmarks_px]
                min_x, max_x = max(0, min(xs)), min(w_frame, max(xs))
                min_y, max_y = max(0, min(ys)), min(h_frame, max(ys))
                pad_x = max(int((max_x - min_x) * 0.15), 15)
                pad_y = max(int((max_y - min_y) * 0.20), 20)

                hx1 = max(0, min_x - pad_x)
                hx2 = min(w_frame, max_x + pad_x)
                hy1 = max(0, min_y - pad_y)
                hy2 = min(h_frame, max_y + pad_y)
        except Exception as mp_err:
            print("[AI WARNING] MediaPipe landmark error:", mp_err)

    # Fallback face detection Tiers 2 & 3
    pose_face_found = False
    if not mp_face_found:
        try:
            pose_results = pose_model(frame, verbose=False, conf=0.12)
            for presult in pose_results:
                if presult.keypoints is None or len(presult.keypoints) == 0:
                    continue
                for kpts in presult.keypoints:
                    conf = kpts.conf[0].cpu().numpy() if kpts.conf is not None else np.ones(5)
                    if np.mean(conf[:5]) > 0.12:
                        pose_face_found = True
                        break
        except Exception:
            pass

    haar_face_found = False
    if not mp_face_found and not pose_face_found and haar_cascade is not None:
        try:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = haar_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(40, 40))
            if len(faces) > 0:
                haar_face_found = True
                fx, fy, fw, fh = faces[0]
                hx1, hy1, hx2, hy2 = fx, fy, fx + fw, fy + fh
        except Exception:
            pass

    raw_face_detected = (mp_face_found or pose_face_found or haar_face_found or (person_box_det is not None))

    # ---------------------------------------------------------
    # 3. TEMPORAL HYSTERESIS FOR FACE DETECTION
    # ---------------------------------------------------------
    if raw_face_detected:
        state["face_detected_counter"] += 1
        state["face_not_detected_counter"] = 0
        if state["face_detected_counter"] >= 1:
            face_status = "FACE DETECTED"
        else:
            face_status = state.get("current_face_status", "FACE DETECTED")
    else:
        state["face_not_detected_counter"] += 1
        state["face_detected_counter"] = 0
        # Require 3 consecutive frames of no face before declaring FACE NOT DETECTED
        if state["face_not_detected_counter"] >= 3:
            face_status = "FACE NOT DETECTED"
        else:
            face_status = state.get("current_face_status", "FACE DETECTED")

    # ---------------------------------------------------------
    # 4. HEAD POSE, EYE GAZE, DROWSINESS ANALYSIS
    # ---------------------------------------------------------
    raw_attention = "LOOKING STRAIGHT"
    raw_eye = "EYES ON SCREEN"
    raw_drowsiness = "ALERT"

    if face_status == "FACE NOT DETECTED":
        raw_attention = "UNKNOWN"
        raw_eye = "UNKNOWN"
        raw_drowsiness = "UNKNOWN"
    elif mp_face_found and len(landmarks_px) >= 478:
        # Landmark indices:
        # Nose tip: 1
        # Left Eye (viewer's right): outer 33, inner 133, top (160, 158), bottom (144, 153), iris 468
        # Right Eye (viewer's left): inner 362, outer 263, top (385, 387), bottom (380, 373), iris 473
        # Face left boundary (viewer's left): 234
        # Face right boundary (viewer's right): 454
        # Chin: 152
        nose = landmarks_px[1]
        face_left = landmarks_px[234]
        face_right = landmarks_px[454]
        chin = landmarks_px[152]

        # -------------------------------------
        # A. Head Orientation (Yaw & Pitch)
        # -------------------------------------
        face_width = max(face_right[0] - face_left[0], 1)
        yaw_ratio = (nose[0] - face_left[0]) / float(face_width)

        eye_mid_y = (landmarks_px[33][1] + landmarks_px[263][1]) / 2.0
        face_height = max(chin[1] - eye_mid_y, 1)
        pitch_ratio = (nose[1] - eye_mid_y) / float(face_height)

        if yaw_ratio > 0.62:
            raw_head_pose = "LOOKING LEFT"
        elif yaw_ratio < 0.38:
            raw_head_pose = "LOOKING RIGHT"
        elif pitch_ratio < 0.20:
            raw_head_pose = "LOOKING AWAY"
        elif pitch_ratio > 0.70:
            raw_head_pose = "LOOKING AWAY"
        else:
            raw_head_pose = "LOOKING STRAIGHT"

        # -------------------------------------
        # B. Eye Aspect Ratio (EAR) & Drowsiness
        # -------------------------------------
        p33 = np.array(landmarks_px[33])
        p133 = np.array(landmarks_px[133])
        p160 = np.array(landmarks_px[160])
        p144 = np.array(landmarks_px[144])
        p158 = np.array(landmarks_px[158])
        p153 = np.array(landmarks_px[153])

        ear_left = (np.linalg.norm(p160 - p144) + np.linalg.norm(p158 - p153)) / (2.0 * max(np.linalg.norm(p33 - p133), 1e-5))

        p362 = np.array(landmarks_px[362])
        p263 = np.array(landmarks_px[263])
        p385 = np.array(landmarks_px[385])
        p380 = np.array(landmarks_px[380])
        p387 = np.array(landmarks_px[387])
        p373 = np.array(landmarks_px[373])

        ear_right = (np.linalg.norm(p385 - p380) + np.linalg.norm(p387 - p373)) / (2.0 * max(np.linalg.norm(p362 - p263), 1e-5))

        avg_ear = (ear_left + ear_right) / 2.0
        EAR_THRESHOLD = 0.20

        if avg_ear < EAR_THRESHOLD:
            state["eyes_closed_counter"] += 1
        else:
            state["eyes_closed_counter"] = 0

        # Require >= 3 consecutive frames with eyes closed to declare SLEEPY / DROWSY
        if state["eyes_closed_counter"] >= 3:
            raw_drowsiness = "SLEEPY / DROWSY"
            raw_eye = "EYES CLOSED"
        elif state["eyes_closed_counter"] >= 1:
            raw_drowsiness = "ALERT" # Normal blink
            raw_eye = "EYES CLOSED"
        else:
            raw_drowsiness = "ALERT"

        # -------------------------------------
        # C. Iris / Eye Gaze Direction (when eyes open)
        # -------------------------------------
        if state["eyes_closed_counter"] == 0:
            iris_l = landmarks_px[468]
            iris_r = landmarks_px[473]

            left_eye_w = max(abs(landmarks_px[133][0] - landmarks_px[33][0]), 1)
            left_iris_pos = (iris_l[0] - min(landmarks_px[33][0], landmarks_px[133][0])) / float(left_eye_w)

            right_eye_w = max(abs(landmarks_px[263][0] - landmarks_px[362][0]), 1)
            right_iris_pos = (iris_r[0] - min(landmarks_px[362][0], landmarks_px[263][0])) / float(right_eye_w)

            avg_iris_ratio = (left_iris_pos + right_iris_pos) / 2.0

            if avg_iris_ratio > 0.62:
                raw_eye = "LOOKING LEFT"
            elif avg_iris_ratio < 0.38:
                raw_eye = "LOOKING RIGHT"
            else:
                raw_eye = "EYES ON SCREEN"

        # -------------------------------------
        # D. Combine Head Pose & Eye Direction for Attention Status
        # -------------------------------------
        if raw_head_pose == "LOOKING LEFT" or raw_eye == "LOOKING LEFT":
            raw_attention = "LOOKING LEFT"
        elif raw_head_pose == "LOOKING RIGHT" or raw_eye == "LOOKING RIGHT":
            raw_attention = "LOOKING RIGHT"
        elif raw_head_pose == "LOOKING AWAY" or raw_eye == "EYES LOOKING AWAY":
            raw_attention = "LOOKING AWAY"
        else:
            raw_attention = "LOOKING STRAIGHT"

        # Draw visual landmark overlays on frame
        cv2.circle(frame, landmarks_px[1], 3, (0, 255, 255), -1)   # Nose
        cv2.circle(frame, landmarks_px[33], 2, (255, 255, 0), -1)  # Left eye outer
        cv2.circle(frame, landmarks_px[263], 2, (255, 255, 0), -1) # Right eye outer
        cv2.circle(frame, landmarks_px[468], 2, (255, 0, 255), -1) # Left iris
        cv2.circle(frame, landmarks_px[473], 2, (255, 0, 255), -1) # Right iris

    else: # Fallback detection without MediaPipe
        raw_head_pose = "LOOKING STRAIGHT"
        raw_attention = "LOOKING STRAIGHT"
        raw_eye = "UNKNOWN"
        raw_drowsiness = "ALERT"

    # ---------------------------------------------------------
    # 5. TEMPORAL SMOOTHING FOR ATTENTION, EYES & DROWSINESS
    # ---------------------------------------------------------
    if face_status == "FACE NOT DETECTED":
        attention_status = "UNKNOWN"
        eye_status = "UNKNOWN"
        drowsiness_status = "UNKNOWN"
    else:
        # Attention status smoothing
        if raw_attention == "LOOKING STRAIGHT":
            state["straight_counter"] += 1
            state["left_turn_counter"] = 0
            state["right_turn_counter"] = 0
            state["away_counter"] = 0
            if state["straight_counter"] >= 1:
                attention_status = "LOOKING STRAIGHT"
            else:
                attention_status = state.get("current_attention_status", "LOOKING STRAIGHT")
        elif raw_attention == "LOOKING LEFT":
            state["left_turn_counter"] += 1
            state["straight_counter"] = 0
            state["right_turn_counter"] = 0
            state["away_counter"] = 0
            if state["left_turn_counter"] >= 2:
                attention_status = "LOOKING LEFT"
            else:
                attention_status = state.get("current_attention_status", "LOOKING STRAIGHT")
        elif raw_attention == "LOOKING RIGHT":
            state["right_turn_counter"] += 1
            state["straight_counter"] = 0
            state["left_turn_counter"] = 0
            state["away_counter"] = 0
            if state["right_turn_counter"] >= 2:
                attention_status = "LOOKING RIGHT"
            else:
                attention_status = state.get("current_attention_status", "LOOKING STRAIGHT")
        elif raw_attention == "LOOKING AWAY":
            state["away_counter"] += 1
            state["straight_counter"] = 0
            state["left_turn_counter"] = 0
            state["right_turn_counter"] = 0
            if state["away_counter"] >= 2:
                attention_status = "LOOKING AWAY"
            else:
                attention_status = state.get("current_attention_status", "LOOKING STRAIGHT")
        else:
            attention_status = raw_attention

        eye_status = raw_eye
        drowsiness_status = raw_drowsiness

    # Update state history
    state["current_face_status"] = face_status
    state["current_attention_status"] = attention_status
    state["current_eye_status"] = eye_status
    state["current_drowsiness_status"] = drowsiness_status
    state["current_phone_status"] = phone_status

    # ---------------------------------------------------------
    # 6. OVERALL ENGAGEMENT SCORE & LEVEL CALCULATION
    # ---------------------------------------------------------
    if face_status == "FACE NOT DETECTED":
        engagement_score = 20.0
        engagement_level = "LOW"
    else:
        score = 100.0
        if phone_status == "PHONE DETECTED":
            score -= 50.0
        if drowsiness_status == "SLEEPY / DROWSY":
            score -= 60.0
        if attention_status == "LOOKING LEFT" or attention_status == "LOOKING RIGHT":
            score -= 25.0
        elif attention_status == "LOOKING AWAY":
            score -= 40.0
        if eye_status == "EYES CLOSED" and drowsiness_status != "SLEEPY / DROWSY":
            score -= 10.0

        engagement_score = max(0.0, min(100.0, score))
        if engagement_score >= 80.0:
            engagement_level = "HIGH"
        elif engagement_score >= 50.0:
            engagement_level = "MODERATE"
        else:
            engagement_level = "LOW"

    state["current_engagement_score"] = engagement_score
    state["current_engagement_level"] = engagement_level

    # Primary attention string for DB compatibility
    if phone_detected_flag:
        primary_attention_level = "PHONE DETECTED"
    elif drowsiness_status == "SLEEPY / DROWSY":
        primary_attention_level = "SLEEPY / DROWSY"
    elif face_status == "FACE NOT DETECTED":
        primary_attention_level = "FACE NOT DETECTED"
    else:
        primary_attention_level = attention_status

    # ---------------------------------------------------------
    # 7. DRAW FACE BOUNDING BOX & STATUS OVERLAY ON FRAME
    # ---------------------------------------------------------
    if hx2 > hx1 and hy2 > hy1 and face_status != "FACE NOT DETECTED":
        color_map = {
            "HIGH": (0, 255, 0),        # Green
            "MODERATE": (0, 165, 255),  # Orange
            "LOW": (0, 0, 255)          # Red
        }
        box_color = color_map.get(engagement_level, (0, 255, 0))
        cv2.rectangle(frame, (hx1, hy1), (hx2, hy2), box_color, 2)
        cv2.putText(
            frame,
            f"{attention_status} ({engagement_level})",
            (hx1, max(hy1 - 10, 25)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            box_color,
            2
        )

    # Print clean backend debug information (Requirement 15)
    print(
        f"[AI DEBUG] Face: {face_status} | Attention: {attention_status} | "
        f"Eyes: {eye_status} | Drowsiness: {drowsiness_status} | Phone: {phone_status} | "
        f"Engagement: {engagement_score}% ({engagement_level})"
    )

    # Structured result dictionary
    result_dict = {
        "face_status": face_status,
        "attention_status": attention_status,
        "eye_status": eye_status,
        "drowsiness_status": drowsiness_status,
        "phone_status": phone_status,
        "phone_detected": phone_detected_flag,
        "engagement_score": engagement_score,
        "engagement_level": engagement_level,
        "attention_level": primary_attention_level
    }

    return frame, phone_detected_flag, primary_attention_level, result_dict