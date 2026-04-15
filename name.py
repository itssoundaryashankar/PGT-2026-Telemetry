
def get_full_name(name, last_name):
    return name + " "  + last_name


name = input("Enter name of student")
father_name = input("enter father name")
mother_name = "Ramya"

full_name = get_full_name(name, father_name)

print(full_name)
full_name = get_full_name(name, mother_name)
print(full_name)

date_of_birth=input("When were u born? For example: 24/3")
current_date= 28


