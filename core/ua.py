import ua_generator

ua = ua_generator.generate()
print(ua)
with open("./core/uagen.py","w") as f:
    f.write(f'UA="{ua}"')