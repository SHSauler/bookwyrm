# fedireads

Social reading and reviewing, decentralized with ActivityPub

## Setting up the developer environment

### Local
You will need postgres installed and running on your computer.

``` bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
createdb fedireads
```

Create the psql user in `psql fedireads`:
``` psql
CREATE ROLE fedireads WITH LOGIN PASSWORD 'fedireads';
GRANT ALL PRIVILEGES ON DATABASE fedireads TO fedireads;
```

Initialize the database (or, more specifically, delete the existing database, run migrations, and start fresh):
``` bash
./rebuilddb.sh
```
This creates two users, `mouse` with password `password123` and `rat` with password `ratword`.

And go to the app at `localhost:8000`

For most testing, you'll want to use ngrok. Remember to set the DOMAIN in `.env` to your ngrok domain.

### Glitch
- On Glitch, New Project > Clone from Git repo -- paste in address of this repo
- Open Glitch console, switch to glitch branch `git checkout glitch`, then `refresh` to update editor
- Copy `.env.example` to `.env`
- In `.env`, change `FEDIREADS_DATABASE_BACKEND` to `sqlite` and `DOMAIN` to the Glitch project's subdomain
- Run `./rebuilddb.sh` in Glitch console
- ??? Profit ???????

## Structure

All the url routing is in `fedireads/urls.py`. This includes the application views (your home page, user page, book page, etc),
application endpoints (things that happen when you click buttons), and federation api endpoints (inboxes, outboxes, webfinger, etc).

The application views and actions are in `fedireads/views.py`. The internal actions call api handlers which deal with federating content.
Outgoing messages (any action done by a user that is federated out), as well as outboxes, live in `fedireads/outgoing.py`, and all handlers for incoming
messages, as well as inboxes and webfinger, live in `fedireads/incoming.py`. Connection to openlibrary.org to get book data is handled in `fedireads/openlibrary.py`.

The UI is all django templates because that is the default. You can replace it with a complex javascript framework over my ~dead body~ mild objections.


## Thoughts and considerations

### What even are books
The most complex part of this is knowing what books are which and who authors are. Right now I'm only using openlibrary.org as a
single, canonical source of truth for books, works, and authors. But it may be that user should be able to import books that aren't
in openlibrary, which, that's hard. So there's room to wonder if the openlibrary work key is indeed how a work should be identified.

The key needs to be universal (or at least universally comprehensible) across all fedireads servers, which is why I'm using an external
identifier controlled by someone else.

### Explain "review"
There's no actual reason to be beholden to simple 5 star reviews with a text body. Are there other ways of thinking about a review
that could be represented in a database?
