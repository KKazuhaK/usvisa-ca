import re
import traceback
from datetime import datetime
from time import sleep
from typing import Union, List

import requests
from selenium import webdriver
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from legacy.gmail import GMail, Message
from legacy_rescheduler import legacy_reschedule
from request_tracker import RequestTracker
from settings import *


def log_message(message: str) -> None:
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {message}")

def get_chrome_driver() -> WebDriver:
    options = webdriver.ChromeOptions()
    if not SHOW_GUI:
        options.add_argument("headless")
        options.add_argument("window-size=1920x1080")
        options.add_argument("disable-gpu")
        options.add_argument('user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36')
    options.add_experimental_option("detach", DETACH)
    options.add_argument('--incognito')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument(f'--user-data-dir=/tmp/chrome-{datetime.now().strftime("%Y%m%d-%H%M%S")}')
    driver = webdriver.Chrome(options=options)
    return driver


def login(driver: WebDriver) -> None:
    driver.get(LOGIN_URL)
    timeout = TIMEOUT

    email_input = WebDriverWait(driver, timeout).until(
        EC.visibility_of_element_located((By.ID, "user_email"))
    )
    email_input.send_keys(USER_EMAIL)

    password_input = WebDriverWait(driver, timeout).until(
        EC.visibility_of_element_located((By.ID, "user_password"))
    )
    password_input.send_keys(USER_PASSWORD)

    policy_checkbox = WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable((By.CLASS_NAME, "icheckbox"))
    )
    policy_checkbox.click()

    login_button = WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable((By.NAME, "commit"))
    )
    login_button.click()


def _find_visible_action(driver: WebDriver, label: str):
    locators = (
        (By.LINK_TEXT, label),
        (By.XPATH, f"//button[normalize-space()='{label}']"),
        (By.XPATH, f"//input[@value='{label}']"),
    )
    for locator in locators:
        for element in driver.find_elements(*locator):
            if element.is_displayed() and element.is_enabled():
                return element
    return False


def _click_action_if_present(
    driver: WebDriver, label: str, timeout: float
) -> bool:
    try:
        action = WebDriverWait(driver, timeout).until(
            lambda current_driver: _find_visible_action(current_driver, label)
        )
    except TimeoutException:
        return False
    action.click()
    sleep(2)
    return True


def _has_visible_element(driver: WebDriver, locator) -> bool:
    return any(
        element.is_displayed()
        for element in driver.find_elements(*locator)
    )


def _appointment_page_state(driver: WebDriver):
    if _has_visible_element(
        driver, (By.ID, "appointments_consulate_appointment_date_input")
    ):
        return "ready"
    if _find_visible_action(driver, "Schedule Appointment"):
        return "schedule"
    if _has_visible_element(driver, (By.CLASS_NAME, "icheckbox")):
        return "policy"
    return False


def _prepare_appointment_page(driver: WebDriver) -> None:
    timeout = TIMEOUT
    for _ in range(3):
        state = WebDriverWait(driver, timeout).until(_appointment_page_state)
        if state == "ready":
            return
        if state == "schedule":
            _click_action_if_present(driver, "Schedule Appointment", timeout)
            continue

        policy_checkbox = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.CLASS_NAME, "icheckbox"))
        )
        policy_checkbox.click()
        continue_button = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.NAME, "commit"))
        )
        continue_button.click()

    WebDriverWait(driver, timeout).until(
        lambda current_driver: _appointment_page_state(current_driver) == "ready"
    )


def get_appointment_page(driver: WebDriver) -> None:
    timeout = TIMEOUT

    # Newer flows show a group-action page with this link. Older flows first
    # show a Continue link and then expose Schedule Appointment.
    if not _click_action_if_present(driver, "Schedule Appointment", 2):
        if not _click_action_if_present(driver, "Continue", timeout):
            raise TimeoutException(
                "Could not find either 'Schedule Appointment' or 'Continue'"
            )
        _click_action_if_present(driver, "Schedule Appointment", timeout)

    current_url = driver.current_url
    schedule_match = re.search(r"/schedule/(\d+)", current_url)
    if not schedule_match:
        raise RuntimeError(f"Could not find schedule id in URL: {current_url}")

    appointment_url = APPOINTMENT_PAGE_URL.format(id=schedule_match.group(1))
    driver.get(appointment_url)


def get_available_dates(
    driver: WebDriver, request_tracker: RequestTracker
) -> Union[List[datetime.date], None]:
    request_tracker.log_retry()
    request_tracker.retry()
    schedule_base = driver.current_url.split("/appointment")[0]
    request_url = schedule_base + "/appointment" + AVAILABLE_DATE_REQUEST_SUFFIX
    request_header_cookie = "".join(
        [f"{cookie['name']}={cookie['value']};" for cookie in driver.get_cookies()]
    )
    request_headers = REQUEST_HEADERS.copy()
    request_headers["Cookie"] = request_header_cookie
    request_headers["User-Agent"] = driver.execute_script("return navigator.userAgent")
    try:
        response = requests.get(request_url, headers=request_headers)
    except Exception as e:
        log_message(f"Get available dates request failed: {e}")
        return None
    if response.status_code != 200:
        log_message(f"Failed with status code {response.status_code}")
        log_message(f"Response Text: {response.text}")
        return None
    try:
        dates_json = response.json()
    except:
        log_message("Failed to decode json")
        log_message(f"Response Text: {response.text}")
        return None
    dates = [datetime.strptime(item["date"], "%Y-%m-%d").date() for item in dates_json]
    return dates


def reschedule(driver: WebDriver, retryCount: int = 0) -> bool:
    date_request_tracker = RequestTracker(
        retryCount if (retryCount > 0) else DATE_REQUEST_MAX_RETRY,
        DATE_REQUEST_DELAY * retryCount if (retryCount > 0) else DATE_REQUEST_MAX_TIME
    )
    while date_request_tracker.should_retry():
        dates = get_available_dates(driver, date_request_tracker)
        if not dates:
            log_message("Error occured when requesting available dates")
            sleep(DATE_REQUEST_DELAY)
            continue
        earliest_available_date = dates[0]
        earliest_acceptable_date = datetime.strptime(EARLIEST_ACCEPTABLE_DATE, "%Y-%m-%d").date()
        latest_acceptable_date = datetime.strptime(LATEST_ACCEPTABLE_DATE, "%Y-%m-%d").date()
        if earliest_acceptable_date <= earliest_available_date <= latest_acceptable_date:
            # Check if the earliest available date falls in any of the excluded date ranges
            for i, (start, end) in enumerate(EXCLUSION_DATE_RANGES, 1):
                if datetime.strptime(start, "%Y-%m-%d").date() <= earliest_available_date <= datetime.strptime(end, "%Y-%m-%d").date():
                    log_message(f"UH OH! Date falls in excluded date range: {start} to {end}")
                    sleep(DATE_REQUEST_DELAY)
                    continue
            log_message(f"FOUND SLOT ON {earliest_available_date}!!!")
            try:
                if legacy_reschedule(driver, earliest_available_date):
                    gmail = GMail(f"{GMAIL_SENDER_NAME} <{GMAIL_EMAIL}>", GMAIL_APPLICATION_PWD)
                    msg = Message(
                        f"Visa Appointment Rescheduled for {earliest_available_date}",
                        to=f"{RECEIVER_NAME} <{RECEIVER_EMAIL}>",
                        text=f"Your visa appointment has been successfully rescheduled to {earliest_available_date} at {USER_CONSULATE} consulate."
                    )
                    gmail.send(msg)
                    gmail.close()
                    log_message("SUCCESSFULLY RESCHEDULED!!!")
                    return True
                return False
            except Exception as e:
                log_message(f"Rescheduling failed: {e}")
                traceback.print_exc()
                continue
        else:
            log_message(f"Earliest available date is {earliest_available_date}")
        sleep(DATE_REQUEST_DELAY)
    return False


def reschedule_with_new_session(retryCount: int = DATE_REQUEST_MAX_RETRY) -> bool:
    driver = get_chrome_driver()
    session_failures = 0
    timeout = TIMEOUT
    while session_failures < NEW_SESSION_AFTER_FAILURES:
        try:
            login(driver)
            get_appointment_page(driver)
            _prepare_appointment_page(driver)
            break
        except Exception as e:
            log_message(f"Unable to get appointment page at {driver.current_url}: {e}")
            session_failures += 1
            sleep(FAIL_RETRY_DELAY)
            continue
    rescheduled = reschedule(driver, retryCount)
    driver.quit()
    if rescheduled:
        return True
    else:
        return False


if __name__ == "__main__":
    session_count = 0
    log_message(f"Attempting to reschedule for email: {USER_EMAIL}")
    log_message(f"User Consulate: {USER_CONSULATE}")
    log_message(f"Earliest Acceptable Date: {EARLIEST_ACCEPTABLE_DATE}")
    log_message(f"Latest Acceptable Date: {LATEST_ACCEPTABLE_DATE}")

    if EXCLUSION_DATE_RANGES:
        log_message("Excluded Date Ranges:")
        for i, (start, end) in enumerate(EXCLUSION_DATE_RANGES, 1):
            log_message(f"  Range {i}: {start} to {end}")
    else:
        log_message("No date ranges excluded")

    while True:
        session_count += 1
        log_message(f"Attempting with new session #{session_count}")
        rescheduled = reschedule_with_new_session()
        sleep(NEW_SESSION_DELAY)
        if rescheduled:
            break
    gmail = GMail(f"{GMAIL_SENDER_NAME} <{GMAIL_EMAIL}>", GMAIL_APPLICATION_PWD)
    msg = Message(
        f"Rescheduler Program Exited",
        to=f"{RECEIVER_NAME} <{RECEIVER_EMAIL}>",
        text=f"The rescheduler program has exited on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}."
    )
    gmail.send(msg)
    gmail.close()
