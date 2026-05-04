from hashing import hash_password, verify_password
import hashlib
import os
import base64


def run_tests():
    print("--- Starting Security Tests ---")

    # Test 1: New password registration and login
    password = "my_super_secret_password"
    print("\n1. Testing hash creation (New OWASP 600k iterations)...")
    stored_hash = hash_password(password)
    print(f"Hash created: {stored_hash}")

    print("Checking correct password...")
    if verify_password(password, stored_hash):
        print("OK: Password matched!")
    else:
        print("FAIL: Password did not match.")

    # Test 2: Wrong password attempt
    print("\n2. Testing wrong password...")
    if not verify_password("wrong_password", stored_hash):
        print("OK: System rejected incorrect password.")
    else:
        print("FAIL: System accepted wrong password!")

    # Test 3: Compatibility with old 260,000 iterations
    print("\n3. Testing backward compatibility (Legacy 260k iterations)...")
    old_iterations = 260000
    test_old_pass = "old_user_password"

    # Manually creating an 'old style' hash to simulate legacy DB record
    salt = os.urandom(16)
    # Replicating the logic of your original _normalize_password
    norm = hashlib.sha256(test_old_pass.encode("utf-8")).digest()
    dk = hashlib.pbkdf2_hmac("sha256", norm, salt, old_iterations)

    # Formatting as it would look in the old database
    fake_old_hash = f"$pbkdf2-sha256${old_iterations}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"

    if verify_password(test_old_pass, fake_old_hash):
        print("OK: Legacy hash successfully verified!")
    else:
        print("FAIL: New code cannot read legacy hash.")


if __name__ == "__main__":
    run_tests()
