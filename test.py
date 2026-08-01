from app.irt.theta import update_theta

theta = 0.0

print("Initial theta:", theta)

theta = update_theta(theta, -1, True)
print("Easy Correct:", theta)

theta = update_theta(theta, 0, True)
print("Medium Correct:", theta)

theta = update_theta(theta, 1, False)
print("Hard Wrong:", theta)