import resend
resend.api_key = ""
try:
    resend.Emails.send({
        "from": "onboarding@resend.dev",
        "to": "onboarding@resend.dev",
        "subject": "Test",
        "html": "test"
    })
except Exception as e:
    print("Resend empty key error:", type(e), e)
