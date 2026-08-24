import argparse
import csv
import random


def generate_random_websites(num_websites, list_of_websites):
    """Generates a list of random websites from the provided list."""
    random_websites = random.sample(list_of_websites, num_websites)
    # Sort the random websites by their number (first element of each sublist)
    random_websites.sort(key=lambda x: int(x[0]))
    return random_websites


def generate_websites(starting_point, end_point, list_of_websites):
    """Generates a list of non-random websites from the provided list."""
    return list_of_websites[starting_point:end_point]


def main():
    parser = argparse.ArgumentParser(description="Generate a list of websites from a CSV file.")
    parser.add_argument("csv_file", help="Path to the CSV file containing the list of websites.")
    parser.add_argument("num_websites", type=int, help="Number of websites to generate.")
    parser.add_argument("--random", action="store_true", help="Generate random websites instead of sequential ones.")
    parser.add_argument(
        "--starting-point", type=int, default=0, help="Starting point for generating sequential websites."
    )
    parser.add_argument("--end-point", type=int, help="End point for generating sequential websites.")

    args = parser.parse_args()
    list_of_websites = []

    # Read the CSV file and extract the list of websites
    with open(args.csv_file, "r") as csvfile:
        reader = csv.reader(csvfile)
        for row in reader:
            list_of_websites.append([row[0], row[1]])
    if args.random:
        websites = generate_websites(args.starting_point, args.end_point, list_of_websites)
        generated_websites = generate_random_websites(args.num_websites, websites)
    else:
        generated_websites = generate_websites(args.starting_point, args.end_point, list_of_websites)

    # Save the generated websites to a new CSV file
    output_file = (
        f"{args.starting_point}_to_{args.end_point}_websites.csv"
        if not args.random
        else f"{args.num_websites}_{args.starting_point}_to_{args.end_point}_random_websites.csv"
    )
    with open(output_file, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        for website in generated_websites:
            writer.writerow(website)

    print(f"Generated {len(generated_websites)} websites and saved to {output_file}")


if __name__ == "__main__":
    main()
