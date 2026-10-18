/**
 * EngageAI - Teacher Create Class Form Validation
 * Validates date, start time (12-hour AM/PM), and end time (12-hour AM/PM)
 * both in real-time and upon form submission.
 */

(function () {
    function initClassFormValidation() {
        const form = document.querySelector(".class-form") || document.getElementById("createClassForm");
        if (!form) return;

        // Prevent double attachment
        if (form.dataset.validationAttached === "true") return;
        form.dataset.validationAttached = "true";

        const dateInput = form.querySelector("#class_date");
        const startTimeInput = form.querySelector("#start_time");
        const endTimeInput = form.querySelector("#end_time");
        const subjectInput = form.querySelector("#subject");
        const courseInput = form.querySelector("#course");
        const semesterSelect = form.querySelector("#semester");

        const dateError = document.getElementById("dateError");
        const startTimeError = document.getElementById("startTimeError");
        const endTimeError = document.getElementById("endTimeError");
        const subjectError = document.getElementById("subjectError");
        const courseError = document.getElementById("courseError");
        const semesterError = document.getElementById("semesterError");

        /**
         * Parses 12-hour AM/PM time into { hours, minutes, totalMinutes }
         * Accepts formats like "09:30 AM", "9:30 AM", "09:30am", "9:30AM"
         */
        function parse12HourTime(timeStr) {
            if (!timeStr || typeof timeStr !== "string") return null;
            const trimmed = timeStr.trim();
            const match = trimmed.match(/^(0?[1-9]|1[0-2]):([0-5][0-9])(?::([0-5][0-9]))?\s*([AaPp][Mm])$/);
            if (!match) return null;

            let hours = parseInt(match[1], 10);
            const minutes = parseInt(match[2], 10);
            const meridian = match[4].toUpperCase();

            if (meridian === "AM") {
                if (hours === 12) hours = 0;
            } else {
                if (hours !== 12) hours += 12;
            }

            return { hours, minutes, totalMinutes: hours * 60 + minutes };
        }

        /**
         * Parses date string (YYYY-MM-DD, DD-MM-YYYY, or DD/MM/YYYY) into Date object
         */
        function parseDateString(dateStr) {
            if (!dateStr || typeof dateStr !== "string") return null;
            const trimmed = dateStr.trim();

            // YYYY-MM-DD
            let m = trimmed.match(/^(\d{4})-(\d{2})-(\d{2})$/);
            if (m) {
                return new Date(parseInt(m[1], 10), parseInt(m[2], 10) - 1, parseInt(m[3], 10));
            }
            // DD-MM-YYYY
            m = trimmed.match(/^(\d{2})-(\d{2})-(\d{4})$/);
            if (m) {
                return new Date(parseInt(m[3], 10), parseInt(m[2], 10) - 1, parseInt(m[1], 10));
            }
            // DD/MM/YYYY
            m = trimmed.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
            if (m) {
                return new Date(parseInt(m[3], 10), parseInt(m[2], 10) - 1, parseInt(m[1], 10));
            }

            return null;
        }

        function formatDateToYMD(d) {
            const year = d.getFullYear();
            const month = String(d.getMonth() + 1).padStart(2, "0");
            const day = String(d.getDate()).padStart(2, "0");
            return `${year}-${month}-${day}`;
        }

        function setFieldError(input, errorEl, message) {
            if (input) {
                input.classList.add("is-invalid");
                input.style.borderColor = "#DC2626";
                input.style.borderWidth = "2px";
            }
            if (errorEl) {
                errorEl.textContent = message;
                errorEl.style.display = "block";
            }
        }

        function clearFieldError(input, errorEl) {
            if (input) {
                input.classList.remove("is-invalid");
                input.style.borderColor = "#ccc";
                input.style.borderWidth = "1px";
            }
            if (errorEl) {
                errorEl.textContent = "";
                errorEl.style.display = "none";
            }
        }

        function validateDate() {
            if (!dateInput) return { valid: true };
            const val = dateInput.value.trim();
            clearFieldError(dateInput, dateError);

            if (!val) {
                return { valid: false, message: "Please select a class date." };
            }

            const parsedDate = parseDateString(val);
            if (!parsedDate || isNaN(parsedDate.getTime())) {
                const msg = "Invalid class date format. Please select a valid date.";
                setFieldError(dateInput, dateError, msg);
                return { valid: false, message: msg };
            }

            const today = new Date();
            const todayYMD = formatDateToYMD(today);
            const selectedYMD = formatDateToYMD(parsedDate);

            if (selectedYMD < todayYMD) {
                const msg = "Class date cannot be in the past.";
                setFieldError(dateInput, dateError, msg);
                return { valid: false, message: msg };
            }

            return { valid: true, selectedYMD, todayYMD, isToday: selectedYMD === todayYMD };
        }

        function validateStartTime() {
            if (!startTimeInput) return { valid: true };
            const val = startTimeInput.value.trim();
            clearFieldError(startTimeInput, startTimeError);

            if (!val) {
                return { valid: false, message: "Please enter a start time (e.g. 09:30 AM)." };
            }

            const parsedStart = parse12HourTime(val);
            if (!parsedStart) {
                const msg = "Invalid start time format. Please use 12-hour format (e.g. 09:30 AM).";
                setFieldError(startTimeInput, startTimeError, msg);
                return { valid: false, message: msg };
            }

            // Check against current time if date is today
            const dateRes = validateDate();
            if (dateRes.valid && dateRes.isToday) {
                const now = new Date();
                const currentMinutes = now.getHours() * 60 + now.getMinutes();
                if (parsedStart.totalMinutes <= currentMinutes) {
                    const msg = "Start time has already passed. Please select a future time.";
                    setFieldError(startTimeInput, startTimeError, msg);
                    return { valid: false, message: msg };
                }
            }

            return { valid: true, parsedStart };
        }

        function validateEndTime() {
            if (!endTimeInput) return { valid: true };
            const endVal = endTimeInput.value.trim();
            clearFieldError(endTimeInput, endTimeError);

            if (!endVal) {
                return { valid: false, message: "Please enter an end time (e.g. 11:00 AM)." };
            }

            const parsedEnd = parse12HourTime(endVal);
            if (!parsedEnd) {
                const msg = "Invalid end time format. Please use 12-hour format (e.g. 11:00 AM).";
                setFieldError(endTimeInput, endTimeError, msg);
                return { valid: false, message: msg };
            }

            const startVal = startTimeInput ? startTimeInput.value.trim() : "";
            const parsedStart = parse12HourTime(startVal);

            if (parsedStart) {
                if (parsedEnd.totalMinutes <= parsedStart.totalMinutes) {
                    const msg = "End time must be later than the start time.";
                    setFieldError(endTimeInput, endTimeError, msg);
                    return { valid: false, message: msg };
                }
            }

            return { valid: true, parsedEnd };
        }

        function validateRequiredText(input, errorEl, fieldName) {
            if (!input) return true;
            clearFieldError(input, errorEl);
            if (!input.value.trim()) {
                setFieldError(input, errorEl, `Please enter a ${fieldName}.`);
                return false;
            }
            return true;
        }

        function revalidateAll() {
            let isValid = true;
            const dRes = validateDate();
            if (!dRes.valid && dateInput && dateInput.value.trim()) {
                setFieldError(dateInput, dateError, dRes.message);
                isValid = false;
            }

            const sRes = validateStartTime();
            if (!sRes.valid && startTimeInput && startTimeInput.value.trim()) {
                setFieldError(startTimeInput, startTimeError, sRes.message);
                isValid = false;
            }

            const eRes = validateEndTime();
            if (!eRes.valid && endTimeInput && endTimeInput.value.trim()) {
                setFieldError(endTimeInput, endTimeError, eRes.message);
                isValid = false;
            }

            return isValid;
        }

        // Real-time revalidation triggers
        if (dateInput) {
            dateInput.addEventListener("change", function () {
                validateDate();
                if (startTimeInput && startTimeInput.value.trim()) {
                    validateStartTime();
                }
                if (endTimeInput && endTimeInput.value.trim()) {
                    validateEndTime();
                }
            });
            dateInput.addEventListener("input", function () {
                validateDate();
                if (startTimeInput && startTimeInput.value.trim()) {
                    validateStartTime();
                }
            });
        }

        if (startTimeInput) {
            startTimeInput.addEventListener("input", function () {
                validateStartTime();
                if (endTimeInput && endTimeInput.value.trim()) {
                    validateEndTime();
                }
            });
            startTimeInput.addEventListener("blur", function () {
                validateStartTime();
                if (endTimeInput && endTimeInput.value.trim()) {
                    validateEndTime();
                }
            });
        }

        if (endTimeInput) {
            endTimeInput.addEventListener("input", function () {
                validateEndTime();
            });
            endTimeInput.addEventListener("blur", function () {
                validateEndTime();
            });
        }

        if (subjectInput) {
            subjectInput.addEventListener("input", function () {
                if (subjectInput.value.trim()) clearFieldError(subjectInput, subjectError);
            });
        }

        if (courseInput) {
            courseInput.addEventListener("input", function () {
                if (courseInput.value.trim()) clearFieldError(courseInput, courseError);
            });
        }

        if (semesterSelect) {
            semesterSelect.addEventListener("change", function () {
                if (semesterSelect.value) clearFieldError(semesterSelect, semesterError);
            });
        }

        // Form Submit Handler
        form.addEventListener("submit", function (e) {
            let isFormValid = true;
            let firstInvalid = null;

            if (subjectInput && !subjectInput.value.trim()) {
                setFieldError(subjectInput, subjectError, "Please enter a subject.");
                if (!firstInvalid) firstInvalid = subjectInput;
                isFormValid = false;
            }

            if (courseInput && !courseInput.value.trim()) {
                setFieldError(courseInput, courseError, "Please enter a course.");
                if (!firstInvalid) firstInvalid = courseInput;
                isFormValid = false;
            }

            if (semesterSelect && !semesterSelect.value) {
                setFieldError(semesterSelect, semesterError, "Please select a semester.");
                if (!firstInvalid) firstInvalid = semesterSelect;
                isFormValid = false;
            }

            const dRes = validateDate();
            if (!dRes.valid) {
                setFieldError(dateInput, dateError, dRes.message);
                if (!firstInvalid) firstInvalid = dateInput;
                isFormValid = false;
            }

            const sRes = validateStartTime();
            if (!sRes.valid) {
                setFieldError(startTimeInput, startTimeError, sRes.message);
                if (!firstInvalid) firstInvalid = startTimeInput;
                isFormValid = false;
            }

            const eRes = validateEndTime();
            if (!eRes.valid) {
                setFieldError(endTimeInput, endTimeError, eRes.message);
                if (!firstInvalid) firstInvalid = endTimeInput;
                isFormValid = false;
            }

            if (!isFormValid) {
                e.preventDefault();
                e.stopPropagation();
                if (firstInvalid) {
                    firstInvalid.focus();
                }
                return false;
            }
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initClassFormValidation);
    } else {
        initClassFormValidation();
    }
})();
